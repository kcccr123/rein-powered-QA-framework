using System;
using System.IO;
using UnityEngine;
using UnityEngine.Events;
using UnityEngine.SceneManagement;

namespace Kcccr.QaFramework
{
    /// <summary>Selects the assigned build scene before the adapter starts gameplay.</summary>
    public sealed class QaPlatformBootstrap : MonoBehaviour
    {
        private void Start()
        {
            try
            {
                var assignment = QaTrialAssignment.Read();
                string reference = assignment.environment.scene;
                int selected = -1;
                for (int index = 0; index < SceneManager.sceneCountInBuildSettings; index++)
                {
                    string path = SceneUtility.GetScenePathByBuildIndex(index);
                    if (reference != path && reference != Path.GetFileNameWithoutExtension(path))
                        continue;
                    if (selected != -1)
                        throw new InvalidOperationException("Ambiguous environment.scene: " + reference);
                    selected = index;
                }
                if (selected < 0 || selected == gameObject.scene.buildIndex)
                    throw new InvalidOperationException("environment.scene must select a gameplay scene in this build: " + reference);

                // sceneLoaded runs after OnEnable and before gameplay components' Start.
                UnityAction<Scene, LoadSceneMode> loaded = null;
                loaded = (scene, mode) =>
                {
                    SceneManager.sceneLoaded -= loaded;
                    InitializeScene(assignment);
                };
                SceneManager.sceneLoaded += loaded;
                try
                {
                    SceneManager.LoadScene(selected, LoadSceneMode.Single);
                }
                catch
                {
                    SceneManager.sceneLoaded -= loaded;
                    throw;
                }
            }
            catch (Exception error)
            {
                Debug.LogError("QA platform bootstrap failed: " + error.Message);
                QaPlatformAdapter.QuitPlayer(1);
            }
        }

        private static void InitializeScene(QaTrialAssignment assignment)
        {
            try
            {
                QaPlatformAdapter selectedAdapter = null;
                foreach (var adapter in FindObjectsByType<QaPlatformAdapter>(FindObjectsSortMode.None))
                {
                    if (!adapter.isActiveAndEnabled)
                        continue;
                    if (selectedAdapter != null)
                        throw new InvalidOperationException("Expected exactly one enabled platform adapter.");
                    selectedAdapter = adapter;
                }
                if (selectedAdapter == null)
                    throw new InvalidOperationException("The assigned scene has no enabled platform adapter.");
                selectedAdapter.SelectAssignment(assignment);
                Debug.Log("QA platform selected scene " + assignment.environment.scene);
            }
            catch (Exception error)
            {
                Debug.LogError("QA platform bootstrap failed: " + error.Message);
                QaPlatformAdapter.QuitPlayer(1);
            }
        }
    }
}
