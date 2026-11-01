# QA Framework

A reusable Unity unit adapter and Python QA evaluator with a TCP startup handshake.

```text
+------------------------------------------------------------------+
| SCENE STARTUP                                                    |
| Launcher starts Python -> validate TCP readiness -> enable game. |
+------------------------------------------------------------------+
             |
             v
+------------------------------------------------------------------+
| DEVELOPER: unit lifecycle                                        |
| StartUnit -> run game -> EndUnit.                                |
| Aggregate final actor snapshots in OnUnitEnd.                    |
+------------------------------------------------------------------+
             |
             v
+------------------------------------------------------------------+
| SHARED QA TRANSPORT                                              |
| Inherited SendReport -> JSON over the same TCP connection.       |
+------------------------------------------------------------------+
             |
             v
+---------------------------------------------------------------------+
| DEVELOPER: Python consumer                                          |
| Receive -> evaluate named requirements -> optionally store results. |
+---------------------------------------------------------------------+
```

The developer calls `StartUnit(unitId)` and `EndUnit()`. The adapter invokes developer hooks and sends the final report. It does no per-tick state collection or end-condition polling. Unity owns actors, simulation, model inference, and unit lifecycle; the shared RL framework owns training.

## Initial package setup

Install Python and this Python package into your chosen environment:

```shell
python -m pip install -e path/to/rein-powered-QA-framework
```

Install the Unity package from `Unity/package.json` through Package Manager. Supply the interpreter, consumer script, and packages on each test machine, including desktop builds.

## Per-game implementation

Derive a component from `Kcccr.QaFramework.QaUnitAdapter`, add it explicitly to your scene controller, and implement:

```csharp
protected abstract void OnUnitStart();
protected abstract QaUnitReport OnUnitEnd();
```

Use `OnUnitStart()` to initialize tracking and component references. In `OnUnitEnd()`, read your `IQaStateSource<TState>` components, aggregate their snapshots, and return a concrete serializable `QaUnitReport` subclass with a typed `evidence` field. The base fills `schema_version`, `unit_id`, and a fresh `attempt_id`, then calls inherited `SendReport()` automatically.

Call `StartUnit(unitId)` from your game's unit-start event and `EndUnit()` at its chosen end condition, before resetting or destroying the sources. End the active attempt before starting another. Repeated or reentrant `EndUnit()` calls do nothing after that attempt is consumed. Disabling/re-enabling the component preserves attempt state and never starts or ends a unit. Delivery runs asynchronously in report order to `127.0.0.1:8800` by default.

## Start the recorder from Unity

Add **QA Framework > QA Recorder Launcher** to an active scene GameObject. Set its Python executable, script arguments (or `-m your_module`), and working directory. Assign its **Adapter**, or keep the unit adapter on the same GameObject. The adapter supplies the TCP host/port and owns the connection. Keep the gameplay controller disabled in the saved scene; connect **On Ready** to that controller's `enabled = true` and **On Stopped** to `enabled = false` in the Inspector.

Your Python consumer runs its normal `QAReportReceiver.receive()` loop. The receiver automatically sends a newline-delimited readiness message when it accepts Unity's connection: `{"type":"ready","schema_version":2,"process_id":1234,"session_id":"..."}`. The launcher gives its child a fresh `QA_RECORDER_SESSION` environment value; the receiver echoes it as `session_id`. The launcher retries refused connections while Python starts, validates the schema and session, then invokes **On Ready**. That same connection carries subsequent unit reports. No readiness print statement is needed; stdout/stderr are ordinary Unity Console logs.

Disabling the launcher, unloading the scene, or stopping Play closes the connection and stops its owned Python process (including Windows virtual-environment child processes). Missing Python, an occupied port, incompatible readiness data, an unexpected session, early exit, or the startup timeout leaves gameplay paused. Python sends readiness once per accepted connection; unit reports flow Unity to Python, with no per-report acknowledgement or gameplay commands.

Use `{timestamp}` in the arguments for a fresh results filename per scene session. Relative working directories start at the Unity project root in the Editor, or beside the executable in desktop builds. The optional **Python Path** adds one source directory to `PYTHONPATH`, relative to the working directory; otherwise install this Python package into the chosen interpreter. Python must already be installed. Desktop builds also need the configured scripts/package beside the build or at configured external paths.

For the included printing example, set **Arguments** to `-u path/to/receive_unity_reports.py` using the path from the configured working directory. Use the selected interpreter's installed QA package, or set **Python Path** to its source directory. Your own consumer supplies requirements and persistence.

## Python

From this repository:

```shell
python -m pip install -e ".[test]"
python examples/receive_unity_reports.py --port 8800
```

The launcher can start this receiver for you; manual startup remains available. The receiving example prints validated reports without assuming any evidence fields. To produce QA results, supply your own named predicates:

```python
from qa_framework import QAReportReceiver, evaluate_unit

with QAReportReceiver(port=8800) as receiver:
    report = receiver.receive()
    result = evaluate_unit(report, {
        "target_activated": lambda unit: unit.evidence["activated"],
        "within_interaction_budget": lambda unit: unit.evidence["interaction_count"] <= 3,
    })
    print(result.to_dict())
```

`receive()` returns a `UnitReport` containing `unit_id`, `attempt_id`, and developer-defined evidence. Schema version 2 validates nonempty string identities and finite JSON objects. Evidence is copied and frozen so later mutations cannot change the received snapshot. Dictionaries/arrays are exported as ordinary JSON objects/arrays by `to_dict()`.

`evaluate_unit(report, requirements)` requires at least one named predicate. Each must return a boolean; overall `passed` is true exactly when all requirements pass. Completion, failure, seeds, recipes, player metrics, and ending reasons have no built-in QA meaning. Add them to your evidence and predicates when needed.

For an offline example and the existing checks:

```shell
python examples/evaluate_unit.py
python -m pytest
```

The old action/observation, level, and episode models are deprecated and retained in their source modules for reference. They are not exported by the package API or imported by the receiver/evaluator. Generic JSON and primitive validation are the only schema helpers loaded by the current runtime. Runtime code uses only the Python standard library and Unity's built-in `JsonUtility`.

See [the integration guide](docs/integration-readiness.md) for a complete developer adapter example, wire format, and verification steps.

## Worked example

The sibling game's [QA integration guide](../kawaii-power-little-adventure/QA/README.md) demonstrates an adapter, state wrappers, lifecycle wiring, and an exit-time/success recorder. Its gameplay, evidence fields, predicates, and batch settings are integration choices.
