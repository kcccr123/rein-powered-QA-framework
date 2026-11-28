using System;
using Kcccr.QaFramework;
using Kcccr.UnityReinforcementLearning;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class BuildDungeonTrials
{
    private const string Scenes = "Assets/Scenes/";
    private const string Game = "Assets/PCGDungeon/";

    [MenuItem("QA/Build dungeon trial environments")]
    public static void Build()
    {
        string[] paths = { Scenes + "Bootstrap.unity", Scenes + "MixedRooms.unity", Scenes + "Cathedral.unity" };
        // Resolve serialized scene references before building the headless Player.
        RequireDependency(paths[1], Game + "Prefabs/Structure/Player.prefab");
        ValidateCathedral(paths[2]);
        BuildPlayer(paths, "Build/Linux/DungeonTrials.x86_64");
    }

    [MenuItem("QA/Build cathedral training environment")]
    public static void BuildTraining()
    {
        const string source = Scenes + "Cathedral.unity";
        const string training = Scenes + "CathedralTraining.unity";
        const string bootstrap = Scenes + "TrainingBootstrap.unity";
        ValidateCathedral(source);

        // Use the exact dungeon and authored settings from the trial scene.
        var scene = EditorSceneManager.OpenScene(source);
        var trial = UnityEngine.Object.FindFirstObjectByType<CathedralTrial>();
        var original = new SerializedObject(trial);
        var actor = trial.gameObject;
        actor.name = "Cathedral training";
        var environment = actor.AddComponent<CathedralTrainingEnvironment>();
        var environmentSettings = new SerializedObject(environment);
        foreach (string field in new[] { "manager", "generator", "cathedralSettings" })
            environmentSettings.FindProperty(field).objectReferenceValue = original.FindProperty(field).objectReferenceValue;
        environmentSettings.ApplyModifiedPropertiesWithoutUndo();
        UnityEngine.Object.DestroyImmediate(trial);
        var host = new SerializedObject(actor.AddComponent<RlTrainingHost>());
        host.FindProperty("environment").objectReferenceValue = environment;
        host.FindProperty("autoStart").boolValue = false;
        host.ApplyModifiedPropertiesWithoutUndo();
        actor.AddComponent<CathedralTraining>();
        if (!EditorSceneManager.SaveScene(scene, training, saveAsCopy: true))
            throw new InvalidOperationException("Could not save the cathedral training scene.");

        var entry = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        new GameObject("QA training bootstrap").AddComponent<QaTrainingBootstrap>();
        if (!EditorSceneManager.SaveScene(entry, bootstrap))
            throw new InvalidOperationException("Could not save the cathedral training bootstrap.");
        BuildPlayer(new[] { bootstrap, training }, "Build/Training/CathedralTraining.x86_64");
    }

    private static void ValidateCathedral(string scene)
    {
        RequireDependency(scene, Game + "Settings/Settings.asset");
        RequireDependency(scene, Game + "Settings/Cathedral Settings.asset");
        RequireDependency(scene, Game + "Prefabs/Structure/CathedralArch.fbx");
        RequireDependency(scene, Game + "Prefabs/Structure/CathedralColumn.prefab");
    }

    private static void BuildPlayer(string[] paths, string output)
    {
        var appUi = AssetDatabase.LoadAssetAtPath<Unity.AppUI.Core.AppUISettings>("Assets/Settings/AppUISettings.asset");
        EditorBuildSettings.AddConfigObject("com.unity.dt.app-ui", appUi, true);
        EditorBuildSettings.scenes = Array.ConvertAll(paths, path => new EditorBuildSettingsScene(path, true));
        PlayerSettings.productName = "Dungeon Navigation Trials";
        PlayerSettings.SetScriptingBackend(NamedBuildTarget.Standalone, ScriptingImplementation.Mono2x);
        var build = BuildPipeline.BuildPlayer(new BuildPlayerOptions
        {
            scenes = paths,
            locationPathName = output,
            target = BuildTarget.StandaloneLinux64,
            options = BuildOptions.None,
            extraScriptingDefines = new[] { "APP_UI_EDITOR_ONLY" }
        });
        if (build.summary.result != BuildResult.Succeeded)
            throw new InvalidOperationException("Dungeon Player build failed: " + build.summary.result);
    }

    private static void RequireDependency(string scene, string asset)
    {
        if (Array.IndexOf(AssetDatabase.GetDependencies(scene), asset) < 0)
            throw new InvalidOperationException(scene + " is missing its reference to " + asset);
    }
}
