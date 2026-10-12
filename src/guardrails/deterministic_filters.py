"""Deterministic guardrails (layer 1 of defence in depth; ISTR rule 6).

Regex/rule checks that run before any LLM sees an input and again on outputs.
They cannot be talked around by prompt injection; the LLM-based compliance agent (WORKFLOW_COMPLIANCE_NODE)
agent is the complementary semantic layer.

Every check is registered by name in :data:`CHECKS` so agent specs can opt in
(``guardrails.input: [prompt_injection]``) and the loader can reject unknown names.

ISTR-specific output checks:
- ``tipping_off``    customer-facing notification language (rule 3)
- ``guilt_language`` statements of guilt rather than suspicion (rule 2)
- ``filing_action``  claims that a report was filed/submitted externally (draft-only)

Output checks are negation- and hedge-aware: "do not inform the customer" or
"may have engaged in" must not trip a block.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

# --- Pattern banks -----------------------------------------------------------

_INJECTION = [
    r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts|rules)",
    r"disregard\s+(the\s+)?(system|previous|above)\b",
    r"forget\s+(everything|your\s+instructions|the\s+rules)",
    r"you\s+are\s+now\s+(a|an|in)\b",
    r"\bact\s+as\s+(?:a\s+)?(?:dan|jailbreak|developer\s+mode)",
    r"new\s+instructions?\s*:",
    r"system\s+prompt\s*[:=]",
    r"reveal\s+(your\s+)?(system\s+prompt|instructions|hidden)",
    r"override\s+(your\s+)?(safety|guardrails|policy)",
    r"pretend\s+(that\s+)?you\s+(are|have)\b",
]

_DESTRUCTIVE = [
    r"\b(delete|drop|truncate|wipe|erase|destroy|purge)\b.*\b(table|database|db|records?|files?|accounts?|data|users?|logs?)\b",
    r"\brm\s+-rf\b",
    r"\b(exfiltrate|leak|steal|dump)\b.*\b(data|records?|credentials?|secrets?)\b",
    r"\b(disable|bypass)\b.*\b(security|auth|authentication|logging|audit)\b",
]

PII_PATTERNS: dict[str, str] = {
    "EMAIL": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
    "EMIRATES_ID": r"\b784-?\d{4}-?\d{7}-?\d\b",
    "IBAN": r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,3})?\b",
    "CREDIT_CARD": r"\b(?:\d[ -]?){13,16}\b",
    "PHONE": r"(?:\+|\b00)\d{1,3}[\s-]?\d{1,3}[\s-]?\d{3}[\s-]?\d{3,4}\b|\b05\d[\s-]?\d{3}[\s-]?\d{4}\b",
    "SSN": r"\b\d{3}-\d{2}-\d{4}\b",
}

_TIPPING_OFF = [
    r"\b(inform|notify|tell|alert|advise|warn|let|disclose\s+to|reach\s+out\s+to|contact)\b[^.\n]{0,40}"
    r"\b(customer|client|subject|account\s+holder)\b[^.\n]{0,60}"
    r"\b(investigation|report|istr|str|sar|suspicio\w*|alert|flagged|monitored|monitoring)\b",
    r"\b(you|your\s+account)\s+(are|is|has\s+been)\s+(under\s+investigation|being\s+investigated|flagged|reported)\b",
    r"\b(dear\s+(customer|client|sir|madam))\b[^\n]{0,200}\b(investigation|report|suspicio\w*)",
]

_GUILT = [
    r"\b(is|are|was|were)\s+(guilty|a\s+criminal|a\s+money\s+launderer|money\s+launderers)\b",
    r"\b(committed|has\s+committed|engaged\s+in|carried\s+out)\s+(money\s+laundering|terrorist\s+financing|fraud)\b",
    r"\bproven\s+to\s+(have|be)\b",
    r"\b(definitely|certainly|conclusively)\s+(laundered|laundering|criminal)\b",
]

_FILING = [
    r"\b(has|have|was|were|been)\s+(filed|submitted|sent|reported|transmitted)\s+(to|with|via)\s+(the\s+)?(fiu|goaml|regulator|authorities|central\s+bank|police)\b",
    r"\b(filed|submitted|sent|transmitted)\s+(it\s+|the\s+report\s+)?(to|with|via)\s+(the\s+)?(fiu|goaml)\b",
    r"\b(str|sar|istr)\s+(has\s+been|was)\s+(filed|submitted)\b",
]

# A match preceded (same sentence, bounded window) by a negation is not a violation.
_NEGATION = re.compile(
    r"\b(not|never|no|nothing|don't|do\s+not|must\s+not|mustn't|should\s+not|shall\s+not|without|avoid|"
    r"prohibit\w*|forbid\w*|refrain)\b",
    re.IGNORECASE,
)
# Hedged phrasing ("may have engaged in") expresses suspicion, not guilt: only
# relevant to the guilt check. A hedge must NOT excuse tipping-off ("you may inform...").
_HEDGE = re.compile(r"\b(may|might|could|possibly|potential(ly)?|suspected|alleged(ly)?|whether)\b", re.IGNORECASE)
_NEGATION_WINDOW = 40


def _compile(patterns: list[str], flags: int = re.IGNORECASE) -> list[re.Pattern[str]]:
    return [re.compile(p, flags) for p in patterns]


_INJECTION_RE = _compile(_INJECTION)
_DESTRUCTIVE_RE = _compile(_DESTRUCTIVE)
_PII_RE = {label: re.compile(p) for label, p in PII_PATTERNS.items()}
_TIPPING_RE = _compile(_TIPPING_OFF)
_GUILT_RE = _compile(_GUILT)
_FILING_RE = _compile(_FILING)


def _search_unnegated(regexes: list[re.Pattern[str]], text: str, label: str,
                      allow_hedge: bool = False) -> list[str]:
    hits: list[str] = []
    excusers = [_NEGATION, _HEDGE] if allow_hedge else [_NEGATION]
    for r in regexes:
        for m in r.finditer(text):
            # Look back to the start of the current sentence (bounded window).
            window_start = max(0, m.start() - _NEGATION_WINDOW)
            prefix = re.split(r"[.!?\n]", text[window_start:m.start()])[-1]
            if any(x.search(prefix) or x.search(m.group(0)) for x in excusers):
                continue
            hits.append(f"{label}: '{m.group(0)[:80]}'")
            break
    return hits


# --- Detectors (text -> list of violation strings) ----------------------------

def detect_prompt_injection(text: str) -> list[str]:
    return [f"prompt_injection: matched '{r.pattern}'" for r in _INJECTION_RE if text and r.search(text)]


def detect_destructive_intent(text: str) -> list[str]:
    return [f"destructive_intent: matched '{r.pattern}'" for r in _DESTRUCTIVE_RE if text and r.search(text)]


def detect_pii(text: str) -> list[str]:
    return [f"pii: {label} detected" for label, r in _PII_RE.items() if text and r.search(text)]


def detect_tipping_off(text: str) -> list[str]:
    return _search_unnegated(_TIPPING_RE, text or "", "tipping_off")


def detect_guilt_language(text: str) -> list[str]:
    return _search_unnegated(_GUILT_RE, text or "", "guilt_language", allow_hedge=True)


def detect_filing_action(text: str) -> list[str]:
    return _search_unnegated(_FILING_RE, text or "", "filing_action")


def redact_pii(text: str) -> str:
    """Replaces PII spans with ``[REDACTED_<TYPE>]`` tokens (outputs, logs, traces)."""
    if not text or not isinstance(text, str):
        return text
    for label, r in _PII_RE.items():
        text = r.sub(f"[REDACTED_{label}]", text)
    return text


CHECKS: dict[str, Callable[[str], list[str]]] = {
    "prompt_injection": detect_prompt_injection,
    "destructive_intent": detect_destructive_intent,
    "pii": detect_pii,
    "tipping_off": detect_tipping_off,
    "guilt_language": detect_guilt_language,
    "filing_action": detect_filing_action,
}

DEFAULT_INPUT_CHECKS = ("prompt_injection", "destructive_intent", "pii")
DEFAULT_OUTPUT_CHECKS = ("tipping_off", "guilt_language", "filing_action")


@dataclass
class GuardrailResult:
    allowed: bool
    violations: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)

    @property
    def reason(self) -> str:
        return "; ".join(self.violations) if self.violations else "OK"


def run_checks(text: str, checks: tuple[str, ...] | list[str]) -> GuardrailResult:
    violations: list[str] = []
    categories: list[str] = []
    for name in checks:
        found = CHECKS[name](text)
        if found:
            categories.append(name)
            violations.extend(found)
    return GuardrailResult(allowed=not violations, violations=violations, categories=categories)


def screen_input(text: str, checks: tuple[str, ...] | list[str] = DEFAULT_INPUT_CHECKS) -> GuardrailResult:
    """Hard gate for incoming requests (workflow start node + agent input guardrails)."""
    return run_checks(text, checks)


def screen_output(text: str, checks: tuple[str, ...] | list[str] = DEFAULT_OUTPUT_CHECKS) -> GuardrailResult:
    """Output gate: tipping-off, guilt language, external-filing claims."""
    return run_checks(text, checks)
