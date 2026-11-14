# Platform refactor

Status: proposed design. The existing foundation below is implemented; the platform additions are not.

## Goal

Build a small, reusable QA platform for Unity:

- Run independent trials in parallel, including comparisons between agent/model versions.
- Let developers define test scenarios, state components, evidence, and success rules.
- Collect each trial's evidence and verifier results, aggregate repeated trials, and report useful feedback.

## Trial definition

One trial means one agent version attempting one scenario once. A suite expands into scenarios x agent versions x repetitions.

The proposed starting boundary is one fresh Unity Player instance per trial, packaged with its Python evaluation consumer in one disposable container. The existing `StartUnit() -> gameplay -> EndUnit()` lifecycle runs inside that container. Container execution is planned; it is not implemented yet.

```text
Run manager supplies scenario + agent version + seed
                            |
                            v
+--------------------------------------------------------+
| ONE TRIAL CONTAINER                                    |
|                                                        |
|  Built Unity game + selected agent                     |
|          |                                             |
|          v                                             |
|  StartUnit -> run test -> EndUnit                      |
|                              |                         |
|                              v                         |
|                   Evidence sent to Python              |
|                              |                         |
|                              v                         |
|                    Run custom verifiers                |
|                              |                         |
|                              v                         |
|                   Save result + evidence               |
+--------------------------------------------------------+
                            |
                            v
              Platform collects the result
```

The run manager supplies the scenario, agent/model version, seed, and execution budget. The same built image can be reused for many trial containers with different configurations.

The container saves its graded result and evidence to storage the platform can collect before removing the container. `EndUnit()` queues asynchronous delivery today; the trial wrapper must wait for the saved evaluation result before shutting down.

## Existing modular foundation

| Component | What exists |
| --- | --- |
| `QaUnitAdapter` | Developer-triggered `StartUnit()` / `EndUnit()`, attempt IDs, and start/end hooks. |
| `IQaStateSource<T>` | Interface for developer-owned components to expose state snapshots. |
| `QaUnitReport` / `UnitReport` | Shared identity envelope containing developer-defined evidence. |
| TCP sender / `QAReportReceiver` | Unity-to-Python report delivery and schema validation. Each receiver serves one connected sender at a time. |
| `QaRecorderLauncher` | Unity starts a Python consumer and checks readiness before enabling gameplay. It does not launch Unity environments. |
| `evaluate_unit()` | Runs developer-supplied named boolean verifiers; all must pass for overall success. |
| `EvaluationReport` | Original evidence, individual verifier results, and overall pass/fail. |

The integration is live inside Unity, but the framework reads evidence through developer hooks at the end of each unit. Continuous traces require additional instrumentation. Persistence and aggregation currently belong to the consuming application.

## Components to add

| Shared platform component | Responsibility |
| --- | --- |
| Suite configuration | Select scenarios, agent/model versions, repetitions, seeds, budgets, and a concurrency limit. |
| Run manager | Expand the suite into trials, assign run/trial IDs, schedule workers, and track completion. |
| Environment lifecycle | Launch, initialize, reset, and stop independent Unity environments through developer-provided integration hooks. |
| Agent execution integration | Select the requested agent version and connect it to the environment's existing control path. |
| Evidence and evaluation pipeline | Associate reports with trials and verifier versions; retain evidence, results, logs, and optional traces. |
| Results storage | Store trial records and artifact references with scenario, environment, agent, and configuration versions. |
| Aggregation and reporting | Group results by scenario and agent version; compare success rates, failures, timing, and available resource/cost measurements. |

These are proposed responsibilities, not existing APIs.

## Developer-owned modules

- Scenario setup, initial state, and reset behavior.
- Agent implementation and environment controls.
- State-source components and the adapter that combines their evidence.
- Test start/end conditions and execution budgets.
- Named verifiers defining acceptable behavior.

The platform supplies the lifecycle, execution, collection, and reporting around these modules. The existing extension points require code; a visual test-building interface is not implemented.

## Trial execution and reports

Each trial needs its own environment state, communication endpoint, and output location. Concurrency must respect machine resources. The current single-sender receiver needs separate endpoints per concurrent worker or a future transport change.

Store infrastructure failures and verifier errors separately from valid test failures. Report the number of trials contributing to each metric. Compare agent versions on the same scenarios and seeds with comparable budgets; report simulated gameplay time separately from wall-clock duration.

```text
Test suite + agent versions + execution settings
                       |
                       v
                 Run manager
                 /    |    \
                v     v     v
             Trial 1 Trial 2 Trial 3   ... in parallel
                |     |     |
             Prepare independent environments
             Run agents
             Capture evidence
             Run verifiers
                |     |     |
                 \    |    /
                       v
             Store results and traces
                       |
                       v
             Aggregate trial results
                       |
                       v
             Reports and comparisons
```
