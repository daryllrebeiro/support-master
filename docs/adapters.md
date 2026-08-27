# SupportMaster Adapter Architecture & Development Guide

This document describes the modular adapter architecture of SupportMaster (Phases 33–40), outlining how vendor-agnostic stage agents interact with external platforms via capability protocols, how to write and test new adapters, and how to configure tenant topology and adapter bindings.

---

## 1. Architecture Invariants

1. **One Agent per Stage, Many Thin Adapters**: Stage agents contain all domain reasoning and operate exclusively on canonical data models (`SupportCase`, `RepositoryDescriptor`, `TestRunResult`, `CIStatus`, etc.). Adapters contain **zero reasoning** — strictly protocol/format translation and vendor API calls.
2. **Core Safety Skeleton is Non-Configurable**: Core safety gates (`duplicate_work_gate`, `investigation_evidence_join`, `implementation_authorization_gate`, `validation_testing_gate`, `publish_authorization_gate`, `final_audit_gate`, and `autonomous_safety_stop`) cannot be disabled or bypassed by tenant configuration or adapters.
3. **Adapters Cannot Touch Gates**: Adapters are structurally isolated. No adapter method may inspect, alter, or bypass gate decision states or control grants. This is verified by static AST analysis in CI.
4. **Capability-Based Interfaces**: Stages depend on fine-grained capability protocols (`CanFetchCase`, `CanSearchCode`, `CanRunTests`, `CanTriggerCI`, `CanSendNotification`, etc.) rather than vendor-specific classes.
5. **Auditable Receipts**: Every adapter call executes through an `IntegrationGateway` and returns an `ExternalOperationReceipt` for immutable, tamper-evident audit tracing.

---

## 2. Capability Protocols

Defined in [`supportmaster.pipeline.capabilities`](file:///c:/Users/Lenovo%20Laptop/dev/support-master/supportmaster/pipeline/capabilities.py):

| Capability Protocol | Core Methods | Canonical Return Types |
| :--- | :--- | :--- |
| `CanFetchCase` | `fetch_case(case_id: str)` | `tuple[SupportCase \| None, ExternalOperationReceipt]` |
| `CanSearchIssues` | `search_issues(query: str)` | `tuple[list[IssueRecord], ExternalOperationReceipt]` |
| `CanPostComment` | `post_comment(case_id: str, body: str)` | `ExternalOperationReceipt` |
| `CanUpdateCaseStatus` | `update_case_status(case_id: str, status: str)` | `ExternalOperationReceipt` |
| `CanListRepositories` | `list_repositories()` | `tuple[list[RepositoryDescriptor], ExternalOperationReceipt]` |
| `CanSearchCode` | `search_code(query: str, repos: list[str] \| None = None)` | `tuple[list[CodeMatch], ExternalOperationReceipt]` |
| `CanReadFile` | `read_file(repo: str, path: str, ref: str \| None = None)` | `tuple[FileBlob \| None, ExternalOperationReceipt]` |
| `CanOpenPullRequest` | `open_pull_request(repo, title, body, head_branch, base_branch)` | `tuple[PullRequestResult, ExternalOperationReceipt]` |
| `CanRunTests` | `run_tests(repo: str, commit_sha: str, test_targets: list[str] \| None = None)` | `tuple[TestRunResult, ExternalOperationReceipt]` |
| `CanReadCIStatus` | `read_ci_status(run_id: str)` | `tuple[CIStatus, ExternalOperationReceipt]` |
| `CanTriggerCI` | `trigger_ci(pipeline: str, commit_sha: str, parameters: dict \| None = None)` | `tuple[str \| None, ExternalOperationReceipt]` |
| `CanReadMonitoringSignal`| `incidents(service: str)`, `metric(name: str, service: str)` | `tuple[list[IncidentRecord \| MetricSample], ExternalOperationReceipt]` |
| `CanSendNotification` | `send_notification(request: NotificationRequest \| str, channel: str \| None = None)` | `ExternalOperationReceipt` |

---

## 3. Implementing a New Adapter

To implement a new adapter (e.g. for a new issue tracker, VCS, CI system, or notification service):

1. **Create the adapter module** in `supportmaster/integrations/`:
   ```python
   from supportmaster.models.control import ExternalOperationReceipt
   from supportmaster.models.support_case import SupportCase
   from supportmaster.integrations.http import JsonHttpTransport
   from supportmaster.integrations.policy import IntegrationGateway

   class CustomTrackerAdapter:
       def __init__(self, transport: JsonHttpTransport, *, gateway: IntegrationGateway | None = None):
           self.transport = transport
           self.gateway = gateway or IntegrationGateway()

       def fetch_case(self, case_id: str) -> tuple[SupportCase | None, ExternalOperationReceipt]:
           def operation():
               code, payload = self.transport.request("GET", f"/api/tickets/{case_id}")
               # Translate vendor payload to canonical SupportCase
               return ExternalOperationReceipt(...)
           
           return self.gateway.execute(
               permission="READ_ISSUES",
               target=case_id,
               operation_type="FETCH_CASE",
               requested_action="fetch_case",
               operation=operation,
           )
   ```

2. **Register the adapter** with `AdapterRegistry`:
   ```python
   from supportmaster.pipeline.registry import default_registry
   from supportmaster.pipeline.capabilities import CanFetchCase

   default_registry.register(
       "custom_tracker",
       CustomTrackerAdapter,
       capabilities=[CanFetchCase],
       interface_version="capability-v1",
       adapter_version="1.0.0",
       vendor="custom_vendor",
   )
   ```

3. **Verify against interface conformance test suite**:
   Run the dedicated capability conformance test suite in `tests/conformance/`:
   ```powershell
   .\.venv\Scripts\python.exe -m unittest discover -s tests/conformance -v
   ```

---

## 4. Tenant Topology and Binding Configuration

Tenants configure which capability nodes run (`pipeline_topology`) and which registered adapter implements each node (`adapter_bindings`) in their `OrganizationProfile`:

```json
{
  "pipeline_topology": {
    "enabled_capability_nodes": [
      "ticket_intake",
      "evidence_gathering",
      "repository_discovery",
      "repository_investigation",
      "code_change",
      "ci_validation",
      "notification"
    ],
    "optional_nodes_disabled": ["notification"],
    "policy_version": "topology-v1"
  },
  "adapter_bindings": {
    "bindings": {
      "ticket_intake": {"adapter_id": "linear", "connection_ref": "env:LINEAR_API_KEY"},
      "repository_discovery": {"adapter_id": "gitlab", "connection_ref": "env:GITLAB_TOKEN"},
      "ci_validation": {"adapter_id": "gitlab_ci", "connection_ref": "env:GITLAB_TOKEN"}
    },
    "policy_version": "bindings-v1"
  }
}
```
