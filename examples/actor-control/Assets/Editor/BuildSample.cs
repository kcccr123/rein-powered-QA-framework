using System;
using System.IO;
using Kcccr.QaFramework;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

public static class BuildSample
{
    [MenuItem("QA/Build Linux actor sample")]
    public static void Build()
    {
        ConfigureHeadless();
        Directory.CreateDirectory("Assets/Scenes");
        var bootstrap = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        new GameObject("Platform bootstrap").AddComponent<QaPlatformBootstrap>();
        EditorSceneManager.SaveScene(bootstrap, "Assets/Scenes/Bootstrap.unity");
        CreateEnvironment("ShortLane", 0.25f);
        CreateEnvironment("LongLane", 0.5f);
        string[] scenes = { "Assets/Scenes/Bootstrap.unity", "Assets/Scenes/ShortLane.unity", "Assets/Scenes/LongLane.unity" };
        EditorBuildSettings.scenes = Array.ConvertAll(scenes, path => new EditorBuildSettingsScene(path, true));
        PlayerSettings.companyName = "QA Framework";
        PlayerSettings.productName = "Actor Control Sample";
        PlayerSettings.SetScriptingBackend(UnityEditor.Build.NamedBuildTarget.Standalone, ScriptingImplementation.Mono2x);
        string output = Environment.GetEnvironmentVariable("QA_SAMPLE_BUILD_PATH");
        if (string.IsNullOrWhiteSpace(output))
            output = Path.GetFullPath("Build/Linux/ActorSample.x86_64");
        var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
        {
            scenes = scenes, locationPathName = output,
            target = BuildTarget.StandaloneLinux64, options = BuildOptions.None,
            extraScriptingDefines = new[] { "APP_UI_EDITOR_ONLY" }
        });
        if (report.summary.result != BuildResult.Succeeded)
            throw new InvalidOperationException("Sample build failed: " + report.summary.result);
    }

    public static void ConfigureHeadless()
    {
        // Inference Engine's editor UI dependency must not initialize a desktop UI in the Player.
        const string path = "Assets/Settings/AppUISettings.asset";
        var settings = AssetDatabase.LoadAssetAtPath<Unity.AppUI.Core.AppUISettings>(path);
        if (settings == null)
        {
            Directory.CreateDirectory("Assets/Settings");
            settings = ScriptableObject.CreateInstance<Unity.AppUI.Core.AppUISettings>();
            settings.hideFlags = HideFlags.None;
            AssetDatabase.CreateAsset(settings, path);
        }
        settings.editorOnly = true;
        settings.includeShadersInPlayerBuild = false;
        Unity.AppUI.Core.AppUI.settings.editorOnly = true;
        Unity.AppUI.Core.AppUI.settings.includeShadersInPlayerBuild = false;
        EditorBuildSettings.AddConfigObject("com.unity.dt.app-ui", settings, true);
        EditorUtility.SetDirty(settings);
        AssetDatabase.SaveAssets();
    }

    private static void CreateEnvironment(string name, float distance)
    {
        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        var actor = GameObject.CreatePrimitive(PrimitiveType.Cube).AddComponent<SampleActor>();
        actor.name = "Controlled actor";
        actor.Configure(distance);
        var adapter = new GameObject("QA adapter").AddComponent<SampleAdapter>();
        adapter.Configure(actor);
        EditorSceneManager.SaveScene(scene, "Assets/Scenes/" + name + ".unity");
    }
}
