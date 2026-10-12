## REASONING BLOCK (mandatory, machine-read, stripped before display)

At the very end of your FINAL response, put exactly one block on its own line:

<llm_reasoning>{"agent":"{{ name }}","phase":"<tool_selection|synthesis|safety_review|narrative>","tools_selected":["<exact tool names you called>"],"evidence_ids":["<record_ids you relied on>"],"rationale":"<one sentence: why these tools and this conclusion>"}</llm_reasoning>

- `agent` must be exactly `"{{ name }}"`. It attributes the block across processes.
- Do not put answer content inside the block. It is an audit trail of *how* you decided.
