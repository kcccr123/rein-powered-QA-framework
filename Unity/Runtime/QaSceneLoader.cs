using System;
using System.IO;
using UnityEngine.Events;
using UnityEngine.SceneManagement;

namespace Kcccr.QaFramework
{
    internal static class QaSceneLoader
    {
        internal static void Load(string reference, int bootstrapIndex, Action initialize)
        {
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
            if (selected < 0 || selected == bootstrapIndex)
                throw new InvalidOperationException("environment.scene must select a scene in this build: " + reference);

            // Apply parameters after Awake/OnEnable and before gameplay's Start.
            UnityAction<Scene, LoadSceneMode> loaded = null;
            loaded = (scene, mode) =>
            {
                SceneManager.sceneLoaded -= loaded;
                initialize();
            };
            SceneManager.sceneLoaded += loaded;
            try { SceneManager.LoadScene(selected, LoadSceneMode.Single); }
            catch
            {
                SceneManager.sceneLoaded -= loaded;
                throw;
            }
        }
    }
}
