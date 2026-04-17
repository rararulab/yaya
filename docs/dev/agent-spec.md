# Oracle Agent Spec Conformance

All agents and flows under `src/yaya/core/` MUST conform to
[Oracle Agent Spec](https://github.com/oracle/agent-spec) — a portable,
framework-agnostic configuration language for agentic systems.

## Requirements

1. **Definition**: each agent/flow is declared through `PyAgentSpec` types.
   System prompts, LLM config, and inputs are properties of the spec, not
   scattered constants.
2. **Serializable**: every agent/flow must round-trip through JSON and YAML
   without loss. This is the conformance contract.
3. **Typed inputs**: input schemas use JSON Schema via `PyAgentSpec`.
4. **No framework lock-in**: do not reach into a specific runtime
   (LangGraph, AutoGen, …) from `core/`. Runtime adapters live at the edge,
   under `cli/` or a dedicated adapter module, and consume the spec.

## Conformance test (mandatory)

Every PR that adds or modifies an agent/flow MUST include a test that:

```python
def test_my_agent_roundtrips_through_agent_spec():
    agent = build_my_agent()
    as_json = agent.to_json()
    as_yaml = agent.to_yaml()
    assert Agent.from_json(as_json) == agent
    assert Agent.from_yaml(as_yaml) == agent
```

Place it alongside the agent's unit tests in `tests/core/`.

## What NOT to do

- Do NOT hard-code prompts as Python string literals inside business logic —
  they belong in the spec.
- Do NOT import runtime-specific types (`langgraph.*`, `autogen.*`) from
  `core/`.
- Do NOT add an agent/flow without its conformance test — the PR will be
  rejected.
