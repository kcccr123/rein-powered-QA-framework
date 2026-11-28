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
        string[] paths = { Scenes + "Bootstrap.unity", Scenes + "MixedRooms.unity", Scenes + "Cathedral.unity", Scenes + "Caves.unity" };
        // Resolve serialized scene references before building the headless Player.
        RequireDependency(paths[1], Game + "Prefabs/Structure/Player.prefab");
        ValidateCathedral(paths[2]);
        CreateCavesScene(paths[2], paths[3]);
        RequireDependency(paths[3], Game + "Settings/CavesSettings.asset");
        BuildPlayer(paths, "Build/Linux/DungeonTrials.x86_64");
    }

    private static void CreateCavesScene(string source, string destination)
    {
        var scene = EditorSceneManager.OpenScene(source);
        var cathedral = UnityEngine.Object.FindFirstObjectByType<CathedralTrial>();
        var original = new SerializedObject(cathedral);
        var manager = (PcgDungeonManager)original.FindProperty("manager").objectReferenceValue;
        var generator = (Generator)original.FindProperty("generator").objectReferenceValue;
        var trial = new SerializedObject(cathedral.gameObject.AddComponent<CavesTrial>());
        trial.FindProperty("manager").objectReferenceValue = manager;
        trial.FindProperty("generator").objectReferenceValue = generator;
        trial.ApplyModifiedPropertiesWithoutUndo();
        UnityEngine.Object.DestroyImmediate(cathedral);

        var settings = AssetDatabase.LoadAssetAtPath<GenerationSettings>(Game + "Settings/CavesSettings.asset");
        foreach (var component in new MonoBehaviour[] { generator, manager.GetComponent<PcgPolicyRunner>() })
        {
            var serialized = new SerializedObject(component);
            serialized.FindProperty("generationSettings").objectReferenceValue = settings;
            serialized.ApplyModifiedPropertiesWithoutUndo();
        }
        if (!EditorSceneManager.SaveScene(scene, destination, saveAsCopy: true))
            throw new InvalidOperationException("Could not save the Caves trial scene.");
    }

    [MenuItem("QA/Build cathedral training environment")]
    public static void BuildTraining()
    {
        const string source = Scenes + "Cathedral.unity";
        const string training = Scenes + "CathedralTraining.unity";
        const string caves = Scenes + "Caves.unity";
        const string cavesTraining = Scenes + "CavesTraining.unity";
        const string bootstrap = Scenes + "TrainingBootstrap.unity";
        ValidateCathedral(source);
        CreateCavesScene(source, caves);
        CreateTrainingScene<CathedralTrial>(source, training, "manager", "generator", "cathedralSettings");
        CreateTrainingScene<CavesTrial>(caves, cavesTraining, "manager", "generator");

        var entry = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        new GameObject("QA training bootstrap").AddComponent<QaTrainingBootstrap>();
        if (!EditorSceneManager.SaveScene(entry, bootstrap))
            throw new InvalidOperationException("Could not save the training bootstrap.");
        BuildPlayer(new[] { bootstrap, training, cavesTraining }, "Build/Training/CathedralTraining.x86_64");
    }

    private static void CreateTrainingScene<T>(string source, string destination, params string[] fields)
        where T : DungeonNavigationTrial
    {
        var scene = EditorSceneManager.OpenScene(source);
        var trial = UnityEngine.Object.FindFirstObjectByType<T>();
        var original = new SerializedObject(trial);
        var actor = trial.gameObject;
        actor.name = "Dungeon training";
        var environment = actor.AddComponent<CathedralTrainingEnvironment>();
        var environmentSettings = new SerializedObject(environment);
        foreach (string field in fields)
            environmentSettings.FindProperty(field).objectReferenceValue = original.FindProperty(field).objectReferenceValue;
        environmentSettings.ApplyModifiedPropertiesWithoutUndo();
        UnityEngine.Object.DestroyImmediate(trial);
        var host = new SerializedObject(actor.AddComponent<RlTrainingHost>());
        host.FindProperty("environment").objectReferenceValue = environment;
        host.FindProperty("autoStart").boolValue = false;
        host.ApplyModifiedPropertiesWithoutUndo();
        actor.AddComponent<CathedralTraining>();
        if (!EditorSceneManager.SaveScene(scene, destination, saveAsCopy: true))
            throw new InvalidOperationException("Could not save " + destination);
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
