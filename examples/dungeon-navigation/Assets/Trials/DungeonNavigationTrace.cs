using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using UnityEngine;

// Detailed evidence is streamed only for trial assignments that request it.
internal sealed class DungeonNavigationTrace : IDisposable
{
    [Serializable] private struct RouteCheck { public Vector3 target; public string reason, collider; }
    [Serializable] private sealed class Decision
    {
        public int step;
        public Vector3 before, ray_origin;
        public Vector3[] ray_ends = new Vector3[ModelSensorPackage.NavigationRayCount];
        public int[] ray_hits = new int[ModelSensorPackage.NavigationRayCount];
        public DungeonNavigationTrial.MovementSample movement;
        public List<string> events = new();
        public List<RouteCheck> route_checks = new();
        public string frontier_snapshot;
    }

    private readonly int seed;
    private readonly QaNavigator4 navigator;
    private readonly string directory;
    private readonly StreamWriter writer;
    private readonly Decision frame = new();
    private bool wasExhausted;
    public string FileName => $"navigation-{seed}.jsonl.gz";

    public DungeonNavigationTrace(int seed, QaNavigator4 navigator)
    {
        this.seed = seed;
        this.navigator = navigator;
        directory = Environment.GetEnvironmentVariable("QA_OUTPUT_DIR");
        writer = new StreamWriter(new GZipStream(File.Create(Path.Combine(directory, FileName)), System.IO.Compression.CompressionLevel.Fastest));
        navigator.DiagnosticEvent += frame.events.Add;
        navigator.RouteCheck += RecordCheck;
    }

    private void RecordCheck(Vector3 target, string reason, Collider blocker)
    {
        frame.route_checks.Add(new RouteCheck { target = target, reason = reason, collider = blocker == null ? null : blocker.name });
    }

    public void RecordContact(ControllerColliderHit hit)
    {
        if (Mathf.Abs(hit.normal.y) < 0.9f)
            RecordCheck(hit.point, "controller_contact", hit.collider);
    }

    public void BeginAction(int step, Vector3 before, ModelSensorTrace[] rays)
    {
        frame.step = step;
        frame.before = before;
        frame.ray_origin = rays[0].Origin;
        for (int i = 0; i < rays.Length; i++)
        {
            frame.ray_ends[i] = rays[i].Endpoint;
            frame.ray_hits[i] = (int)rays[i].HitId;
        }
        bool exhausted = navigator.Status == "observed_space_exhausted";
        frame.frontier_snapshot = null;
        if (exhausted && !wasExhausted)
        {
            frame.frontier_snapshot = $"frontiers-{seed}-{step}.json";
            File.WriteAllText(Path.Combine(directory, frame.frontier_snapshot), JsonUtility.ToJson(navigator.CaptureFrontiers()));
        }
        wasExhausted = exhausted;
    }

    public void EndAction(DungeonNavigationTrial.MovementSample movement)
    {
        frame.movement = movement;
        writer.WriteLine(JsonUtility.ToJson(frame));
        frame.events.Clear();
        frame.route_checks.Clear();
    }

    public void Dispose()
    {
        navigator.DiagnosticEvent -= frame.events.Add;
        navigator.RouteCheck -= RecordCheck;
        writer.Dispose();
    }
}

internal sealed class DungeonNavigationContacts : MonoBehaviour
{
    public DungeonNavigationTrace Trace { private get; set; }

    private void OnControllerColliderHit(ControllerColliderHit hit) => Trace.RecordContact(hit);
}
