using System;
using System.Collections.Concurrent;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Net.Sockets;
using System.Threading.Tasks;
using UnityEngine;
using UnityEngine.Events;
using Debug = UnityEngine.Debug;

namespace Kcccr.QaFramework
{
    /// <summary>Launches a desktop Python recorder and releases scene startup after its TCP handshake.</summary>
    [AddComponentMenu("QA Framework/QA Recorder Launcher")]
    [DisallowMultipleComponent]
    public sealed class QaRecorderLauncher : MonoBehaviour
    {
        [SerializeField] private string pythonExecutable = "python";
        [Tooltip("Python script or -m module arguments. {timestamp} becomes a fresh UTC session timestamp.")]
        [SerializeField] private string arguments = "-u path/to/record_units.py";
        [Tooltip("Relative paths start at the Unity project root, or beside the desktop executable in a build.")]
        [SerializeField] private string workingDirectory = ".";
        [Tooltip("Optional source directory added to PYTHONPATH, relative to the working directory.")]
        [SerializeField] private string pythonPath = "";
        [Tooltip("Adapter that owns the report connection. Defaults to the adapter on this GameObject.")]
        [SerializeField] private QaUnitAdapter adapter;
        [Min(1f)] [SerializeField] private float startupTimeoutSeconds = 30f;
        [SerializeField] private UnityEvent onReady = new UnityEvent();
        [SerializeField] private UnityEvent onStopped = new UnityEvent();

        private readonly ConcurrentQueue<string> output = new ConcurrentQueue<string>();
        private readonly ConcurrentQueue<string> errors = new ConcurrentQueue<string>();
        private Process recorder;
        private Stopwatch startupClock;
        private string sessionId;

        public bool IsReady { get; private set; }

        private void Start()
        {
            try
            {
                if (adapter == null)
                    adapter = GetComponent<QaUnitAdapter>();
                if (adapter == null)
                    throw new InvalidOperationException("Assign a QA unit adapter to the recorder launcher.");
                string directory = Path.GetFullPath(Path.Combine(Application.dataPath, "..", workingDirectory));
                var info = new ProcessStartInfo
                {
                    FileName = pythonExecutable,
                    Arguments = arguments.Replace("{timestamp}", DateTime.UtcNow.ToString("yyyyMMdd-HHmmss-fffffff")),
                    WorkingDirectory = directory,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true
                };
                info.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";
                sessionId = Guid.NewGuid().ToString("N");
                info.EnvironmentVariables["QA_RECORDER_SESSION"] = sessionId;
                if (!string.IsNullOrWhiteSpace(pythonPath))
                {
                    string path = Path.GetFullPath(Path.Combine(directory, pythonPath));
                    string existing = info.EnvironmentVariables["PYTHONPATH"];
                    if (!string.IsNullOrEmpty(existing))
                        path += Path.PathSeparator + existing;
                    info.EnvironmentVariables["PYTHONPATH"] = path;
                }

                recorder = new Process { StartInfo = info };
                // Process callbacks run off the main thread; Unity logging/events are drained in Update.
                recorder.OutputDataReceived += (_, message) =>
                {
                    if (message.Data != null)
                        output.Enqueue(message.Data);
                };
                recorder.ErrorDataReceived += (_, message) =>
                {
                    if (message.Data != null)
                        errors.Enqueue(message.Data);
                };
                recorder.Start();
                startupClock = Stopwatch.StartNew();
                recorder.BeginOutputReadLine();
                recorder.BeginErrorReadLine();
                ConnectRecorder();
            }
            catch (Exception error) when (error is Win32Exception || error is InvalidOperationException
                || error is IOException || error is ArgumentException)
            {
                Fail("Could not start Python: " + error.Message);
            }
        }

        private void Update()
        {
            while (output.TryDequeue(out string line))
                Debug.Log("QA recorder: " + line, this);
            while (errors.TryDequeue(out string line))
                Debug.LogWarning("QA recorder: " + line, this);

            if (recorder == null)
                return;
            if (recorder.HasExited)
            {
                Fail("Python exited with code " + recorder.ExitCode + ". See the QA recorder output above.");
                return;
            }
            if (!IsReady && recorder != null && startupClock.Elapsed.TotalSeconds >= startupTimeoutSeconds)
                Fail("Python did not complete its TCP readiness handshake within the startup timeout.");
        }

        private async void ConnectRecorder()
        {
            while (recorder != null)
            {
                try
                {
                    await adapter.ConnectAsync();
                }
                catch (SocketException error) when (error.SocketErrorCode == SocketError.ConnectionRefused)
                {
                    // The child may still be importing modules and opening its listener.
                    await Task.Delay(100);
                    continue;
                }
                catch (Exception error) when (error is SocketException || error is IOException
                    || error is ObjectDisposedException || error is InvalidOperationException)
                {
                    if (recorder != null)
                        Fail("TCP readiness handshake failed: " + error.Message);
                    return;
                }
                if (recorder == null)
                    return;
                if (recorder.HasExited)
                    return; // Update reports the process exit and pauses the consumer.
                if (adapter.RecorderSessionId != sessionId)
                {
                    Fail("The TCP receiver belongs to a different process. Free the adapter's port before starting.");
                    return;
                }

                IsReady = true;
                onReady.Invoke();
                return;
            }
        }

        private void Fail(string message)
        {
            Debug.LogError("QA recorder: " + message, this);
            StopRecorder();
        }

        private void OnDisable()
        {
            StopRecorder();
        }

        private void StopRecorder()
        {
            IsReady = false;
            if (adapter != null)
                adapter.CloseConnection();
            if (recorder == null)
                return;

            Process ownedProcess = recorder;
            recorder = null;
            try
            {
                if (!ownedProcess.HasExited)
                {
                    if (Application.platform == RuntimePlatform.WindowsEditor || Application.platform == RuntimePlatform.WindowsPlayer)
                    {
                        // Windows virtual-environment interpreters can launch a child Python process.
                        using (Process cleanup = Process.Start(new ProcessStartInfo
                        {
                            FileName = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "taskkill.exe"),
                            Arguments = "/PID " + ownedProcess.Id + " /T /F",
                            UseShellExecute = false,
                            CreateNoWindow = true,
                            RedirectStandardOutput = true,
                            RedirectStandardError = true
                        }))
                            cleanup.WaitForExit(2000);
                    }
                    else
                    {
                        ownedProcess.Kill();
                    }
                    ownedProcess.WaitForExit(2000);
                }
            }
            catch (InvalidOperationException)
            {
                // Startup may have failed before the process acquired an operating-system handle.
            }
            finally
            {
                ownedProcess.Dispose();
                onStopped.Invoke();
            }
        }
    }
}
