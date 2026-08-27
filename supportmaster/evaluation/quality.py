"""Pre-demo quality pack for deterministic SupportMaster scenarios."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from ..models.evaluation import EvaluationScenario, QualityPackResult
from ..persistence import SQLiteRunStore
from .suite import EndToEndWorkflowSuite, FunctionalEvaluationSuite, load_scenarios


def run_quality_pack(
    store: SQLiteRunStore,
    scenarios: list[EvaluationScenario],
    *,
    suite_name: str = "pre-demo-quality",
) -> QualityPackResult:
    """Run all deterministic checks and summarize coverage and failures."""
    functional = FunctionalEvaluationSuite(store, suite_name=f"{suite_name}:functional").run(scenarios)
    end_to_end = EndToEndWorkflowSuite(store, suite_name=f"{suite_name}:e2e").run(scenarios)
    categories = Counter(tag for scenario in scenarios for tag in scenario.tags)
    checks = Counter()
    failures: list[str] = []
    for result in functional.scenarios:
        for check in result.checks:
            checks[check.name] += 1
            if check.status == "FAIL":
                failures.append(f"{result.scenario_id}:{check.name}")
    for result in end_to_end.simulations:
        for step in result.steps:
            checks[f"e2e:{step.name}"] += 1
            if step.status == "FAIL":
                failures.append(f"{result.scenario_id}:e2e:{step.name}")
    # Phase 40: Modularity & Adapter architecture validation
    try:
        from ..pipeline.registry import default_registry
        from ..pipeline.topology import validate_topology, TopologyValidationError
        from ..pipeline.bindings import validate_bindings, BindingValidationError
        from ..models.organization import PipelineTopology, AdapterBindingsConfig, AdapterBindingEntry

        # 1. Topology rejects skeleton tampering
        try:
            validate_topology(PipelineTopology(enabled_capability_nodes=["ticket_intake", "duplicate_work_gate"]))
            failures.append("modularity:skeleton_tampering_allowed")
        except TopologyValidationError:
            checks["modularity:skeleton_tampering_rejected"] += 1

        # 2. Binding rejects unregistered adapter
        try:
            validate_bindings(
                AdapterBindingsConfig(bindings={"ticket_intake": AdapterBindingEntry(adapter_id="nonexistent")}),
                default_registry,
            )
            failures.append("modularity:unregistered_adapter_allowed")
        except BindingValidationError:
            checks["modularity:unregistered_adapter_rejected"] += 1

        # 3. Valid default topology & registered adapters
        validate_topology(PipelineTopology())
        checks["modularity:default_topology_valid"] += 1
        categories["modularity"] = 3
    except Exception as exc:
        failures.append(f"modularity:error:{exc}")

    return QualityPackResult(
        status="PASS" if functional.status == "PASS" and end_to_end.status == "PASS" and not failures else "FAIL",
        functional=functional,
        end_to_end=end_to_end,
        category_counts=dict(categories),
        check_counts=dict(checks),
        failures=failures,
    )


def run_fixture_quality_pack(
    store: SQLiteRunStore,
    directory: str | Path,
) -> QualityPackResult:
    return run_quality_pack(store, load_scenarios(directory))
