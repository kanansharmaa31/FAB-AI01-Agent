"""MCP service contracts: the tool names each service is expected to expose.

This is the boundary between the mesh and the data services. Agent specs may
only ``allow`` tools listed here (validated by the loader), so a typo or an
over-broad grant fails loudly at load time instead of silently at run time.

When a real GERNAS service (SQL Agent, KG, RAG) replaces a mock, update the
tool list for that service here; nothing else in the mesh needs to change.
``tests/test_mock_services.py`` asserts the mocks implement exactly this surface.
"""
from __future__ import annotations

SERVICE_CATALOG: dict[str, tuple[str, ...]] = {
    "transactions": ("get_transactions", "detect_patterns", "get_counterparties"),
    "kyc": ("get_kyc_profile", "get_expected_activity", "get_risk_rating"),
    "screening": ("screen_customer", "screen_name"),
    "graph": ("get_entity_links", "find_shared_attributes"),
    "policy_rag": ("search_policies",),
}

SERVICE_DESCRIPTIONS: dict[str, str] = {
    "transactions": "Transaction history and deterministic pattern checks (structuring, velocity, round-tripping).",
    "kyc": "KYC/CDD profile, expected activity and customer risk rating.",
    "screening": "Sanctions, PEP and adverse-media screening hits.",
    "graph": "Entity/counterparty relationship graph.",
    "policy_rag": "Grounded, cited retrieval over AML policy and regulatory guidance.",
}
