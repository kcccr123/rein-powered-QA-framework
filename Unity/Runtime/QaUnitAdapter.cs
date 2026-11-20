using System;
using System.IO;
using System.Net.Sockets;
using System.Text;
using System.Threading.Tasks;
using UnityEngine;

namespace Kcccr.QaFramework
{
    /// <summary>Developer-driven unit lifecycle with TCP readiness and ordered Python report delivery.</summary>
    public abstract class QaUnitAdapter : MonoBehaviour
    {
        [SerializeField] private string host = "127.0.0.1";
        [SerializeField] private int port = 8800;

        private string unitId;
        private string attemptId;
        private bool attemptActive;
        private TcpClient client;
        private Task connectionTask;
        private Task pendingSend = Task.CompletedTask;
        private bool active;

        internal string RecorderSessionId { get; private set; }

        /// <summary>Connects to Python and validates its readiness before reports can be sent.</summary>
        public Task ConnectAsync()
        {
            if (!active)
                throw new InvalidOperationException("Enable the QA adapter before connecting.");
            if (connectionTask == null || connectionTask.IsFaulted || connectionTask.IsCanceled)
                connectionTask = ConnectAndHandshakeAsync();
            return connectionTask;
        }

        public void StartUnit(string unitId)
        {
            if (attemptActive)
                throw new InvalidOperationException("End the active unit attempt before starting another.");
            if (string.IsNullOrWhiteSpace(unitId))
                throw new ArgumentException("A unit ID must not be empty.", nameof(unitId));

            this.unitId = unitId;
            attemptId = Guid.NewGuid().ToString("N");
            attemptActive = true;
            OnUnitStart();
        }

        public void EndUnit()
        {
            if (!attemptActive)
                return;

            // Consume this attempt before developer code can reenter EndUnit or start the next unit.
            attemptActive = false;
            string endedUnitId = unitId;
            string endedAttemptId = attemptId;
            QaUnitReport report = OnUnitEnd();
            if (report == null)
                throw new InvalidOperationException("OnUnitEnd must return a unit report.");

            report.schema_version = 2;
            report.unit_id = endedUnitId;
            report.attempt_id = endedAttemptId;
            SendReport(report);
        }

        protected abstract void OnUnitStart();
        protected abstract QaUnitReport OnUnitEnd();

        protected void SendReport(QaUnitReport report)
        {
            // Serialize the concrete report on the main thread before queuing delivery.
            byte[] payload = Encoding.UTF8.GetBytes(JsonUtility.ToJson(report) + "\n");
            pendingSend = SendAfter(pendingSend, payload);
        }

        protected virtual void OnEnable()
        {
            active = true;
        }

        protected virtual void OnDisable()
        {
            active = false;
            CloseConnection();
        }

        private async Task SendAfter(Task previous, byte[] payload)
        {
            await previous;
            if (!active)
                return;

            try
            {
                await ConnectAsync();
                TcpClient connection = client;
                if (!active || connection == null)
                    return;

                await connection.GetStream().WriteAsync(payload, 0, payload.Length);
            }
            catch (Exception error) when (error is SocketException || error is IOException || error is ObjectDisposedException)
            {
                CloseConnection();
                if (active)
                    Debug.LogWarning("QA report delivery failed: " + error.Message, this);
            }
        }

        private async Task ConnectAndHandshakeAsync()
        {
            var connection = new TcpClient { NoDelay = true };
            client = connection;
            try
            {
                await connection.ConnectAsync(host, port);
                using (var reader = new StreamReader(connection.GetStream(), Encoding.UTF8, false, 1024, true))
                {
                    string line = await reader.ReadLineAsync();
                    ReadinessMessage message;
                    try
                    {
                        message = JsonUtility.FromJson<ReadinessMessage>(line);
                    }
                    catch (ArgumentException error)
                    {
                        throw new IOException("Invalid QA readiness handshake.", error);
                    }
                    if (message == null || message.type != "ready" || message.schema_version != 2 || message.process_id <= 0)
                        throw new IOException("Expected a QA readiness handshake for report schema version 2.");
                    if (!active || client != connection)
                        throw new ObjectDisposedException(nameof(QaUnitAdapter));
                    RecorderSessionId = message.session_id;
                }
            }
            catch
            {
                if (client == connection)
                    CloseConnection();
                connection.Close();
                throw;
            }
        }

        internal void CloseConnection()
        {
            client?.Close();
            client = null;
            connectionTask = null;
            RecorderSessionId = null;
        }

        [Serializable]
        private sealed class ReadinessMessage
        {
            public string type;
            public int schema_version;
            public int process_id;
            public string session_id;
        }
    }
}
