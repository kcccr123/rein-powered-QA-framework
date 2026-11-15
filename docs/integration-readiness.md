# Unity units to Python QA

Both integrations share schema-2 unit reports. [Platform trials](#platform-trials) use Python as the parent process and confirm saved output before shutdown. The standalone launcher/consumer flow is:

```text
+------------------------------------------------------------------+
| STARTUP                                                          |
| Python receiver -> TCP readiness once per connection.            |
| Unity validates the launch session -> enables controller.        |
+------------------------------------------------------------------+
             |
             v
+------------------------------------------------------------------+
| DEVELOPER UNIT LIFECYCLE                                         |
| StartUnit -> gameplay -> EndUnit.                                |
| OnUnitEnd aggregates final state snapshots.                      |
+------------------------------------------------------------------+
             |
             v
+------------------------------------------------------------------+
| UNITY QA ADAPTER                                                 |
| QaUnitAdapter.SendReport -> newline JSON on the same connection. |
+------------------------------------------------------------------+
             |
             v
+------------------------------------------------------------------+
| PYTHON QA CONSUMER                                               |
| QAReportReceiver -> evaluate_unit with named predicates.         |
+------------------------------------------------------------------+
```

Python listens on `127.0.0.1:8800`. Unity connects and validates Python's readiness message before sending reports. Python sends one UTF-8 JSON line per accepted connection: `{"type":"ready","schema_version":2,"process_id":1234,"session_id":"..."}`. `schema_version` identifies the supported report schema; `process_id` identifies the receiver process. The receiver echoes `QA_RECORDER_SESSION` from its environment as `session_id` (empty for a manual receiver without that variable). Unity then writes one UTF-8 JSON line per ended attempt on that same connection. Python sends no per-report acknowledgement, gameplay command, or QA result. TCP itself still handles transport acknowledgements.

## Platform trials

For Linux containers, add Linux Build Support (Mono) to Unity 6000.3.4f1 and build
a Linux x86-64 Player. Use CPU inference with `-batchmode -nographics` and set
`SDL_VIDEODRIVER=dummy` in the image to avoid Unity's native input startup crash
when no display driver exists. The [sample build](../examples/actor-control/README.md)
also keeps the inference package's App UI dependency editor-only.

Start the Player in a small scene containing `QaPlatformBootstrap`. Include every target environment in Build Settings, referenced by its full scene path or unique scene name. Missing/ambiguous references and missing/multiple active enabled adapters fail startup. Derive the gameplay scene adapter from `QaPlatformAdapter`, using the state sources and typed reports below plus the [observation/action hooks](../README.md#run-a-platform-trial). Keep one adapter alive throughout the trial and enable the gameplay controller through its **On Ready** event. The game still owns resets and end conditions; the adapter reads `episodes_per_trial` and stops accepting episodes at that limit.

```text
qa-run-trial -- <Unity executable> [arguments]
  -> Python reads QA_TRIAL_CONFIG and opens QA_OUTPUT_DIR/episodes.jsonl
  -> listen on an available loopback port -> start Player with port and session
  -> bootstrap selects environment.scene -> require exactly one enabled adapter
  -> adapter validates Python readiness -> loads agent.model on CPU
  -> zero-input validation inference -> On Ready enables gameplay
  -> CanStartUnit -> StartUnit -> gameplay -> EndUnit -> JSON report
  -> Python validates and appends report with trial context
  -> final EndUnit blocks new starts -> drain queue -> trial_complete
  -> Python flushes + fsyncs + closes output -> trial_saved
  -> Unity exits successfully -> Python exits -> container exits
```

The runtime sets `QA_REPORT_PORT` and `QA_RECORDER_SESSION` in its child's environment. `QA_TRIAL_CONFIG` points to JSON containing nonempty `suite_id`/`trial_id`, a positive integer `episodes_per_trial`, and `environment`/`agent` objects with nonempty string `id` fields. The runtime passes the assignment and output paths as absolute paths. `environment.scene` and `agent.model` are also required. The suite prepares each distinct self-contained ONNX path once in an isolated Editor with Inference Engine 2.4.1; it mounts the selected `.sentis` read-only at `/qa/model/agent.sentis` and puts that path in the generated assignment. Local Player runs must supply a prepared `.sentis` path themselves. Other environment/agent settings remain available to developer code.

The concrete adapter declares `ObservationSize` and `ActionSize`, implements `CollectObservation()` and `ApplyAction(float[] actions)`, and holds its actor reference. One static float input `[1, ObservationSize]` and one float output `[1, ActionSize]` are supported. The adapter owns its CPU worker and validates it with a zero-input pass before **On Ready**, without applying actions. The active episode loop runs from base `FixedUpdate` every configurable 0.1 simulated seconds. `StartUnit` resets its timer; `EndUnit` or replacing an episode invalidates pending actions. Shape/type/nonfinite values, model/inference failures, or observation/action hook exceptions fail the trial and dispose inference resources. Completion and disablement also dispose them. No separate policy-driving component is required.

Messages use one UTF-8 JSON object per line on one connection. Platform readiness extends the standalone handshake without changing the report schema:

```json
{"type":"ready","schema_version":2,"platform_protocol_version":1,"process_id":1234,"session_id":"session-token","suite_id":"suite-1","trial_id":"trial-1","episodes_per_trial":3}
```

After exactly three reports, Unity sends the completion request. Python checks the IDs, count, and distinct attempt IDs before acknowledging the closed output file:

```json
{"type":"trial_complete","suite_id":"suite-1","trial_id":"trial-1","completed_episodes":3}
{"type":"trial_saved","suite_id":"suite-1","trial_id":"trial-1","saved_episodes":3}
```

Each line in `episodes.jsonl` contains the original validated report and its trial context:

```json
{
  "suite_id": "suite-1",
  "trial_id": "trial-1",
  "environment_id": "interaction-room",
  "agent_id": "baseline",
  "episode_index": 1,
  "report": {
    "schema_version": 2,
    "unit_id": "interaction-check",
    "attempt_id": "unique-attempt-id",
    "evidence": {"activated": true, "checkpoints": ["switch", "door"]}
  }
}
```

Python preserves arbitrary finite JSON evidence without game-specific classes or required evidence-schema configuration. It does not run verifiers; use `evaluate_unit()` separately with developer-supplied predicates. An existing output file is rejected, and invalid/duplicate/excess reports fail the trial while preserving records already written.

`StartUnit()` requires `CanStartUnit`; overlapping starts, starts during the end hook, and starts after the limit throw. Repeated or reentrant `EndUnit()` calls do nothing. `CompletedEpisodes` counts queued reports; the final acknowledgement confirms saved records. Call `EndUnit()` before resetting sources, and keep the adapter enabled until it quits the Player. Disablement, hook errors, or delivery failure fail the platform trial. If overriding `Start`, `FixedUpdate`, `OnDisable`, or `OnApplicationQuit`, call the base implementation. Do not use `QaRecorderLauncher` on this path.

Startup and final report-draining/acknowledgement each have a 30-second deadline. Python also requires the completion request within 30 seconds of the final report. After acknowledgement, Unity has five seconds to exit with code zero. Failure or cancellation stops the child, waits two seconds, then kills/reaps it if necessary. Python returns `0` for confirmed storage plus a successful Player exit, `1` for trial failure, `2` for CLI misuse, or `130` for cancellation. No gameplay duration timeout is imposed.

## Automatic recorder startup (standalone)

Use the package's **QA Framework > QA Recorder Launcher** component to start a Python script or `-m module` on scene startup. Configure the executable, arguments, working directory, and optional source directory for `PYTHONPATH`. Python and the consumer must already be available on the machine; the component installs nothing.

Assign the launcher's **Adapter**, or place it on the same GameObject as your unit adapter. The adapter supplies the host/port and owns the shared readiness/report connection. Keep your gameplay controller disabled in the saved scene. Wire **On Ready** to enable it and **On Stopped** to disable it. Run the ordinary Python receive loop: `receive()` accepts Unity's connection and sends readiness automatically before waiting for a report. No special print or game-owned handshake implementation is required. The launcher sets a fresh session value in its child's environment, retries refused connections while Python starts, and checks the receiver's schema and echoed session before releasing gameplay. This also supports interpreters that launch a child process, such as Windows virtual environments. Startup failures and the configurable real-time timeout do not release gameplay. An unexpected recorder exit invokes **On Stopped**.

The launcher mirrors stdout/stderr into the Unity Console and closes the report connection and stops its own child process on disable, scene unload, or stopping Play. `{timestamp}` in arguments expands to a fresh UTC session timestamp, suitable for output filenames. Relative paths resolve from the configured working directory; that directory is relative to the project root in the Editor or the executable directory in desktop builds. Builds require you to supply Python and the scripts/package at those configured paths. Stdout is used for logs; readiness is a TCP message.

See [the package README](../README.md#start-the-recorder-from-unity) for example Inspector values. The unit adapter and evaluation contracts below are unchanged; manual receiver startup also remains supported.

## Unity integration

Install `Unity/package.json` through Unity Package Manager's **Install package from disk** command. The package assembly is `Kcccr.QaFramework.Runtime`, and its namespace is `Kcccr.QaFramework`. It pins Unity Inference Engine 2.4.1 for the platform CPU policy path. The standalone unit/launcher behavior is unchanged. If your consumer uses an assembly definition, reference `Kcccr.QaFramework.Runtime` from it.

The state-source contract is:

```csharp
public interface IQaStateSource<TState>
{
    TState GetState();
}
```

Add your concrete adapter to the scene controller explicitly. Implement the state-source interface on your actor wrapper components. Return a plain snapshot; the framework never finds or polls sources itself. For example, this adapter expects a developer-provided component on the same GameObject implementing `IQaStateSource<InteractionEvidence>`:

```csharp
using System;
using Kcccr.QaFramework;

[Serializable]
public struct InteractionEvidence
{
    public int interaction_count;
    public bool activated;
    public string[] checkpoints;
}

[Serializable]
public sealed class InteractionReport : QaUnitReport
{
    public InteractionEvidence evidence;
}

public sealed class InteractionQaAdapter : QaUnitAdapter
{
    private IQaStateSource<InteractionEvidence> source;

    protected override void OnUnitStart()
    {
        source = GetComponent<IQaStateSource<InteractionEvidence>>();
        // Initialize any developer-owned tracking for this attempt here.
    }

    protected override QaUnitReport OnUnitEnd()
    {
        return new InteractionReport { evidence = source.GetState() };
    }
}
```

For multiple actors, obtain their components in `OnUnitStart()` and combine their final snapshots into your typed evidence in `OnUnitEnd()`. Snapshotting and aggregation happen only when developer code calls `EndUnit()`.

Call `adapter.StartUnit("interaction-check")` when your unit begins, then `adapter.EndUnit()` at the developer-chosen end. `StartUnit` validates a nonempty ID, generates a fresh attempt ID, and calls `OnUnitStart`. Starting while an attempt is active throws `InvalidOperationException`. Every attempt, including retries of the same unit, has a different attempt ID.

`EndUnit` marks the attempt ended before calling `OnUnitEnd`, fills the returned report's identity, and calls protected `SendReport(QaUnitReport report)` automatically. Return the report from your hook; do not also send it there. Repeated or reentrant end calls cannot send the same attempt again. Ending without an active attempt is a no-op. The hook must return a non-null report; a throwing hook or serialization error leaves that attempt ended and propagates to the caller.

Disabling/re-enabling the component changes transport availability, closes its connection, and preserves attempt state. It does not call either unit hook or clear duplicate protection. Call `EndUnit` while the adapter is enabled, before disabling/destroying it or refreshing the unit, and allow asynchronous delivery to finish before shutdown. Pending sends may be discarded when disabled. If you override `OnEnable` or `OnDisable`, call the base implementation.

`QaUnitReport` owns the identity fields. The concrete subclass owns a public typed `evidence` field. The adapter passes the concrete object to `JsonUtility.ToJson`, retaining subclass fields. Use serializable classes/structs and supported fields; dictionaries require other handling and are not supported by `JsonUtility`. See [Unity JSON serialization](https://docs.unity3d.com/6000.3/Documentation/Manual/json-serialization.html).

## Unit record

```json
{
  "schema_version": 2,
  "unit_id": "interaction-check",
  "attempt_id": "9ee6e4270b594cfb93df020861fe95b3",
  "evidence": {
    "interaction_count": 3,
    "activated": true,
    "checkpoints": ["switch", "door"]
  }
}
```

The example is formatted for reading; the TCP record occupies one line. `unit_id` identifies the developer's unit; `attempt_id` identifies this execution of it. Both must be nonempty strings. Evidence must be a finite JSON object, including nested objects, arrays, booleans, strings, numbers, and nulls. No dungeon fields are required. Python rejects malformed or unsupported records; it does not convert validation errors into QA failures. Version 1 episode envelopes are no longer accepted, and there is no compatibility layer.

## QA results

```python
from qa_framework import evaluate_unit

result = evaluate_unit(report, {
    "target_activated": lambda unit: unit.evidence["activated"],
    "within_interaction_budget": lambda unit: unit.evidence["interaction_count"] <= 3,
})
print(result.to_dict())
```

The result contains the original `report`, a boolean per named requirement, and `passed`, which is `all(requirements.values())`. Requirements are mandatory and nonempty; names must be nonempty strings and predicates must return booleans. Predicate errors propagate to the caller. The framework has no default completion check or special failure override. If a game's failure reason should fail QA, supply a predicate for it. Consumers decide how results affect generation or learning.

## Connection behavior

The launcher calls `QaUnitAdapter.ConnectAsync()` before enabling gameplay. Manual consumers can also await this method, or let the first report connect automatically. Each new connection must complete the readiness handshake before reports are written. Unity serializes on the main thread, then sends asynchronously in order. If delivery fails, Unity logs `QA report delivery failed: ...` and closes that connection; the failed report is not retried. A later report can reconnect and consume a new readiness message. Python accepts another connection after a clean disconnect. There is no persistent report queue or per-report application acknowledgement. Connection startup never invokes unit hooks or reads actor snapshots.

## Local verification

Run `python -m pytest` and `python examples/evaluate_unit.py`. For Unity verification, use a temporary consumer project with this local package and developer-owned state components; no persistent test application is needed.

1. Start the Python receiver. Call `StartUnit`, change source state over several frames, and confirm no reports or snapshot reads happen before `EndUnit`. End the unit and confirm the latest snapshots arrive as typed evidence, including an example with no dungeon fields.
2. Call `EndUnit` repeatedly and reentrantly from the hook; expect one report. Disable/re-enable between end calls; expect no duplicate. Disable/re-enable during an active attempt; expect no lifecycle hooks or loss of that attempt. Starting another active attempt must fail.
3. Run two attempts with the same unit ID; confirm different attempt IDs. Send several reports in order on one connection, then reconnect. Confirm explicit predicates produce both passing and failing results, and Python sends exactly one readiness message per connection with no per-report acknowledgement.
4. Send a JSON record in fragmented TCP writes and multiple records in one write; `receive()` must still return one validated report at a time. Reject version 1, empty identities, and nonfinite evidence.

## Worked example

The sibling game's [QA integration](../../kawaii-power-little-adventure/QA/README.md) applies these contracts to dungeon attempts. Use its adapter, evidence, recorder, and lifecycle wiring as an example; choose your own unit boundaries and predicates for another game. Player control and training remain outside the QA receiver/evaluator.
