using System;
using UnityEngine;

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
                QaSceneLoader.Load(assignment.environment.scene, gameObject.scene.buildIndex,
                    () => InitializeScene(assignment));
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
