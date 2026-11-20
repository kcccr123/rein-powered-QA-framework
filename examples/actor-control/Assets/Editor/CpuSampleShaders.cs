using System.Collections.Generic;
using UnityEditor.Build;
using UnityEditor.Rendering;
using UnityEngine;

// This sample always uses CPU inference, including in headless Linux builds.
public sealed class CpuSampleShaders : IPreprocessComputeShaders
{
    public int callbackOrder => 0;
    public void OnProcessComputeShader(ComputeShader shader, string kernelName, IList<ShaderCompilerData> data)
    {
        data.Clear();
    }
}
