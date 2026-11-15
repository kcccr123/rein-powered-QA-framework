using System;
using System.IO;
using System.Net.Sockets;
using System.Text;
using System.Threading.Tasks;
using UnityEngine;
using UnityEngine.Events;
using Unity.InferenceEngine;

namespace Kcccr.QaFramework
{
    /// <summary>Owns CPU policy decisions, episode counting, and confirmed output shutdown.</summary>
    public abstract class QaPlatformAdapter : MonoBehaviour
    {
        private const int ProtocolVersion = 1;
        private const int TimeoutMilliseconds = 30000;

        [Tooltip("Enable gameplay only after the platform runtime's readiness handshake.")]
        [SerializeField] private UnityEvent onReady = new UnityEvent();
        [Tooltip("Simulated seconds between decisions during an active episode.")]
        [SerializeField] private float decisionInterval = 0.1f;

        private QaTrialAssignment assignment;
        private Worker worker;
        private float decisionElapsed;
        private string unitId;
        private string attemptId;
        private bool attemptActive;
        private bool endingUnit;
        private bool finishing;
        private bool failed;
        private bool saved;
        private bool started;
        private bool applicationQuitting;
        private TcpClient client;
        private StreamReader reader;
        private Task pendingSend = Task.CompletedTask;

        public UnityEvent OnReady => onReady;
        public bool IsReady { get; private set; }
        public int CompletedEpisodes { get; private set; }
        public int EpisodeLimit
        {
            get
            {
                if (assignment == null)
                    return 0;
                return assignment.episodes_per_trial;
            }
        }
        public bool CanStartUnit => IsReady && !attemptActive && !endingUnit && !finishing && !failed;
        protected abstract int ObservationSize { get; }
        protected abstract int ActionSize { get; }
        protected abstract float[] CollectObservation();
        protected abstract void ApplyAction(float[] actions);

        internal void SelectAssignment(QaTrialAssignment selected)
        {
            assignment = selected;
        }

        // Platform trials connect to their parent Python runtime; they do not launch a recorder.
        protected virtual async void Start()
        {
            started = true;
            try
            {
                if (assignment == null)
                    throw new InvalidOperationException("The Player must start in a QaPlatformBootstrap scene.");
                string sessionId = Environment.GetEnvironmentVariable("QA_RECORDER_SESSION");
                if (string.IsNullOrWhiteSpace(sessionId))
                    throw new InvalidOperationException("QA_RECORDER_SESSION is required.");
                if (!int.TryParse(Environment.GetEnvironmentVariable("QA_REPORT_PORT"), out int port)
                    || port < 1 || port > 65535)
                    throw new InvalidOperationException("QA_REPORT_PORT must identify the runtime's TCP listener.");

                await WithDeadline(ConnectAsync(port, sessionId), "Python readiness handshake timed out.");
                if (failed)
                    return;
                InitializeModel();
                IsReady = true;
                onReady.Invoke();
            }
            catch (Exception error)
            {
                Fail("Platform startup failed: " + error.Message);
            }
        }

        public void StartUnit(string unitId)
        {
            if (!CanStartUnit)
                throw new InvalidOperationException("Cannot start an episode before readiness, during another episode, or after trial completion begins.");
            if (string.IsNullOrWhiteSpace(unitId))
                throw new ArgumentException("A unit ID must not be empty.", nameof(unitId));

            this.unitId = unitId;
            attemptId = Guid.NewGuid().ToString("N");
            decisionElapsed = 0f;
            attemptActive = true;
            try
            {
                OnUnitStart();
            }
            catch (Exception error)
            {
                Fail("Episode start failed: " + error.Message);
                throw;
            }
        }

        public void EndUnit()
        {
            if (!attemptActive || failed)
                return;

            // Latch the end before calling developer code, including the final-episode limit.
            attemptActive = false;
            endingUnit = true;
            finishing = CompletedEpisodes + 1 == EpisodeLimit;
            try
            {
                QaUnitReport report = OnUnitEnd();
                if (report == null)
                    throw new InvalidOperationException("OnUnitEnd must return a unit report.");
                report.schema_version = 2;
                report.unit_id = unitId;
                report.attempt_id = attemptId;
                byte[] payload = Encode(report);
                CompletedEpisodes++;
                pendingSend = SendAfter(pendingSend, payload);
                if (finishing)
                    FinishTrial();
                else
                    ObserveSend(pendingSend);
            }
            catch (Exception error)
            {
                Fail("Episode report failed: " + error.Message);
                throw;
            }
            finally
            {
                endingUnit = false;
            }
        }

        protected abstract void OnUnitStart();
        protected abstract QaUnitReport OnUnitEnd();

        private void InitializeModel()
        {
            if (ObservationSize <= 0 || ActionSize <= 0 || decisionInterval <= 0f
                || float.IsNaN(decisionInterval) || float.IsInfinity(decisionInterval))
                throw new InvalidOperationException("Observation/action sizes and decision interval must be positive.");
            Model model = ModelLoader.Load(assignment.agent.model);
            if (model.inputs.Count != 1 || model.outputs.Count != 1
                || model.inputs[0].dataType != DataType.Float || !model.inputs[0].shape.IsStatic()
                || model.inputs[0].shape.ToTensorShape() != new TensorShape(1, ObservationSize))
                throw new InvalidOperationException("Expected one float input [1, ObservationSize] and one output.");
            worker = new Worker(model, BackendType.CPU);
            // Validate the output contract before readiness, without observing or moving the actor.
            Infer(new float[ObservationSize]);
            Debug.Log("QA platform initialized CPU model " + assignment.agent.model, this);
        }

        private float[] Infer(float[] observations)
        {
            ValidateVector(observations, ObservationSize, "observation");
            using (var input = new Tensor<float>(new TensorShape(1, ObservationSize), observations))
            {
                worker.Schedule(input);
                var output = worker.PeekOutput() as Tensor<float>;
                if (output == null || output.shape != new TensorShape(1, ActionSize))
                    throw new InvalidOperationException("Expected a float output [1, ActionSize].");
                float[] actions = output.DownloadToArray();
                ValidateVector(actions, ActionSize, "action");
                return actions;
            }
        }

        private static void ValidateVector(float[] vector, int size, string name)
        {
            if (vector == null || vector.Length != size)
                throw new InvalidOperationException("Incorrect " + name + " vector size.");
            foreach (float value in vector)
            {
                if (float.IsNaN(value) || float.IsInfinity(value))
                    throw new InvalidOperationException("Nonfinite " + name + " value.");
            }
        }

        protected virtual void FixedUpdate()
        {
            if (!IsReady || !attemptActive || finishing || failed)
                return;
            decisionElapsed += Time.fixedDeltaTime;
            if (decisionElapsed + 0.000001f < decisionInterval)
                return;
            decisionElapsed %= decisionInterval;
            string decidingAttempt = attemptId;
            try
            {
                float[] observations = CollectObservation();
                // A developer hook can end or replace the episode. Never deliver stale actions.
                if (!attemptActive || attemptId != decidingAttempt || failed || !isActiveAndEnabled)
                    return;
                float[] actions = Infer(observations);
                if (attemptActive && attemptId == decidingAttempt && !failed && isActiveAndEnabled)
                    ApplyAction(actions);
            }
            catch (Exception error)
            {
                Fail("Policy decision failed: " + error.Message);
            }
        }

        private void DisposeInference()
        {
            worker?.Dispose();
            worker = null;
        }

        private async Task ConnectAsync(int port, string sessionId)
        {
            client = new TcpClient { NoDelay = true };
            await client.ConnectAsync("127.0.0.1", port);
            // Keep one reader for the handshake and final acknowledgement so buffered bytes survive.
            reader = new StreamReader(client.GetStream(), Encoding.UTF8, false, 1024, true);
            string line = await reader.ReadLineAsync();
            if (line == null)
                throw new IOException("Python disconnected before readiness.");
            ReadinessMessage ready = JsonUtility.FromJson<ReadinessMessage>(line);
            if (ready == null || ready.type != "ready" || ready.schema_version != 2
                || ready.platform_protocol_version != ProtocolVersion || ready.process_id <= 0
                || ready.session_id != sessionId || ready.suite_id != assignment.suite_id
                || ready.trial_id != assignment.trial_id || ready.episodes_per_trial != EpisodeLimit)
                throw new IOException("Python readiness does not match this platform trial.");
        }

        private static byte[] Encode(object message)
        {
            return Encoding.UTF8.GetBytes(JsonUtility.ToJson(message) + "\n");
        }

        private async Task SendAfter(Task previous, byte[] payload)
        {
            await previous;
            if (failed)
                throw new IOException("Trial has already failed.");
            await client.GetStream().WriteAsync(payload, 0, payload.Length);
        }

        private async void ObserveSend(Task send)
        {
            try
            {
                await send;
            }
            catch (Exception error)
            {
                Fail("Report delivery failed: " + error.Message);
            }
        }

        private async void FinishTrial()
        {
            try
            {
                // One deadline covers draining reports, requesting completion, and receiving the ACK.
                await WithDeadline(ConfirmSavedAsync(), "Python did not confirm saved records within 30 seconds.");
                if (failed)
                    return;
                saved = true;
                IsReady = false;
                DisposeInference();
                Debug.Log("QA platform: saved " + CompletedEpisodes + " episodes; exiting Unity.", this);
                CloseConnection();
                QuitPlayer(0);
            }
            catch (Exception error)
            {
                Fail("Trial completion failed: " + error.Message);
            }
        }

        private async Task ConfirmSavedAsync()
        {
            await pendingSend;
            byte[] payload = Encode(new CompletionMessage
            {
                type = "trial_complete",
                suite_id = assignment.suite_id,
                trial_id = assignment.trial_id,
                completed_episodes = CompletedEpisodes
            });
            await client.GetStream().WriteAsync(payload, 0, payload.Length);
            string line = await reader.ReadLineAsync();
            if (line == null)
                throw new IOException("Python disconnected without confirming saved records.");
            SavedMessage message = JsonUtility.FromJson<SavedMessage>(line);
            if (message == null || message.type != "trial_saved"
                || message.suite_id != assignment.suite_id || message.trial_id != assignment.trial_id
                || message.saved_episodes != EpisodeLimit)
                throw new IOException("Saved acknowledgement does not match this trial.");
        }

        private static async Task WithDeadline(Task operation, string error)
        {
            if (await Task.WhenAny(operation, Task.Delay(TimeoutMilliseconds)) != operation)
                throw new TimeoutException(error);
            await operation;
        }

        private void Fail(string message)
        {
            if (failed || saved)
                return;
            failed = true;
            IsReady = false;
            DisposeInference();
            Debug.LogError("QA platform: " + message, this);
            CloseConnection();
            QuitPlayer(1);
        }

        private void CloseConnection()
        {
            client?.Close();
            reader?.Dispose();
        }

        protected virtual void OnApplicationQuit()
        {
            applicationQuitting = true;
        }

        protected virtual void OnDisable()
        {
            if (started && !saved && !failed && !applicationQuitting)
                Fail("Platform adapter was disabled before trial completion.");
            CloseConnection();
            DisposeInference();
        }

        internal static void QuitPlayer(int exitCode)
        {
#if UNITY_EDITOR
            UnityEditor.EditorApplication.isPlaying = false;
#else
            Application.Quit(exitCode);
#endif
        }

        [Serializable]
        private sealed class ReadinessMessage
        {
            public string type;
            public int schema_version;
            public int platform_protocol_version;
            public int process_id;
            public string session_id;
            public string suite_id;
            public string trial_id;
            public int episodes_per_trial;
        }

        [Serializable]
        private sealed class CompletionMessage
        {
            public string type;
            public string suite_id;
            public string trial_id;
            public int completed_episodes;
        }

        [Serializable]
        private sealed class SavedMessage
        {
            public string type;
            public string suite_id;
            public string trial_id;
            public int saved_episodes;
        }
    }
}
