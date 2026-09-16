"""Self-contained per-agent harnesses for orchestrator_2.

Each subpackage here is a complete "harness": a Python module that
receives a universal STATE envelope from the orchestrator and writes
a policy to ``SOC_POLICY_QUEUE`` on its own (typically by calling an
inner Cortex Agent or doing deterministic Python).

A harness module MUST export a single entry point:

    def run(*, store, session_id: str, player: str, view: Dict[str, Any]) -> Dict[str, Any]

The returned dict is the audit envelope the orchestrator stamps into
:class:`SOC_AGENT_INVOCATION`.

The orchestrator's dispatcher locates a harness via the binding
``locator`` string ``"<module_path>:<callable_name>"``, e.g.
``"sea_of_colours.orchestrator_2.harnesses.tabula_v12.harness:run"``.
"""
