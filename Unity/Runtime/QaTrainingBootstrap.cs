using System;
using System.IO;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace Kcccr.QaFramework
{
    /// <summary>Selects a training scene and publishes readiness without a TCP probe.</summary>
    public sealed class QaTrainingBootstrap : MonoBehaviour
    {
        [Serializable] private sealed class Assignment
        {
            public string node_id;
            public EnvironmentSettings environment;
        }
        [Serializable] private sealed class EnvironmentSettings { public string scene; }
        [Serializable] private sealed class Ready
        {
            public string node_id;
            public string session_id;
            public int port;
        }

        private void Start()
        {
            try
            {
                string json = File.ReadAllText(Environment.GetEnvironmentVariable("QA_TRAIN_CONFIG"));
                var assignment = JsonUtility.FromJson<Assignment>(json);
                if (assignment == null || string.IsNullOrWhiteSpace(assignment.node_id)
                    || assignment.environment == null || string.IsNullOrWhiteSpace(assignment.environment.scene))
                    throw new InvalidOperationException("Training requires node_id and environment.scene.");
                QaSceneLoader.Load(assignment.environment.scene, gameObject.scene.buildIndex, () =>
                {
                    try
                    {
                        QaTrainingAdapter selected = null;
                        foreach (var adapter in FindObjectsByType<QaTrainingAdapter>(FindObjectsSortMode.None))
                        {
                            if (!adapter.isActiveAndEnabled)
                                continue;
                            if (selected != null)
                                throw new InvalidOperationException("Expected exactly one enabled training adapter.");
                            selected = adapter;
                        }
                        if (selected == null)
                            throw new InvalidOperationException("The scene has no enabled training adapter.");
                        int port = selected.ConfigureAndStart(json);
                        if (port < 1 || port > 65535)
                            throw new InvalidOperationException("Training adapter returned an invalid port.");
                        string path = Environment.GetEnvironmentVariable("QA_TRAIN_READY");
                        string session = Environment.GetEnvironmentVariable("QA_TRAIN_SESSION");
                        if (string.IsNullOrWhiteSpace(path) || string.IsNullOrWhiteSpace(session))
                            throw new InvalidOperationException("Training readiness path/session are required.");
                        File.WriteAllText(path + ".tmp", JsonUtility.ToJson(new Ready
                        {
                            node_id = assignment.node_id, session_id = session, port = port
                        }));
                        File.Move(path + ".tmp", path);
                    }
                    catch (Exception error) { Fail(error); }
                });
            }
            catch (Exception error) { Fail(error); }
        }

        private static void Fail(Exception error)
        {
            Debug.LogError("QA training bootstrap failed: " + error.Message);
            QaPlatformAdapter.QuitPlayer(1);
        }
    }
}
