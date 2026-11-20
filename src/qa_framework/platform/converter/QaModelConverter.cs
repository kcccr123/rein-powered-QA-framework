using System;
using System.IO;
using Unity.InferenceEngine;
using UnityEditor;
using UnityEngine;

public static class QaModelConverter
{
    [Serializable] private sealed class Entry { public string source; public string artifact; }
    [Serializable] private sealed class Manifest { public Entry[] models; }

    public static void Convert()
    {
        try
        {
            var manifest = JsonUtility.FromJson<Manifest>(File.ReadAllText("conversion.json"));
            int index = 0;
            foreach (var entry in manifest.models)
            {
                string assetPath = "Assets/model-" + (++index) + ".onnx";
                File.Copy(entry.source, assetPath, true);
                bool importFailed = false;
                Application.LogCallback inspectLog = (message, trace, type) =>
                {
                    if (type == LogType.Error || type == LogType.Exception || type == LogType.Assert)
                        importFailed = true;
                };
                Application.logMessageReceived += inspectLog;
                Model model;
                try
                {
                    AssetDatabase.ImportAsset(assetPath, ImportAssetOptions.ForceSynchronousImport);
                    var asset = AssetDatabase.LoadAssetAtPath<ModelAsset>(assetPath);
                    if (importFailed || asset == null)
                        throw new InvalidOperationException("ONNX import failed: " + entry.source);
                    model = ModelLoader.Load(asset);
                }
                finally
                {
                    Application.logMessageReceived -= inspectLog;
                }
                ModelWriter.Save(entry.artifact, model);
                Debug.Log("QA converted " + entry.source + " -> " + entry.artifact);
            }
        }
        catch (Exception error)
        {
            Debug.LogException(error);
            EditorApplication.Exit(1);
        }
    }
}
