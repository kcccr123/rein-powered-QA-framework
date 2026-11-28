using System;
using System.Collections.Generic;
using System.IO;
using Kcccr.QaFramework;
using UnityEngine;

public sealed class CavesTrial : DungeonNavigationTrial
{
    [SerializeField] private PcgDungeonManager manager;
    [SerializeField] private Generator generator;
    private int seedOffset;

    [Serializable] private sealed class Assignment { public TrialEnvironment environment; }
    [Serializable] private sealed class TrialEnvironment { public Parameters parameters; }
    [Serializable] private sealed class Parameters
    {
        public int seed_offset, action_limit, stall_memory_actions;
        public bool use_combat_and_loot, explore_known_floor;
        public float[] policy_action_std;
    }

    private void Awake()
    {
        manager.RemoteControlActive = true;
        MaxActions = 3600;
        GridCellWidth = 0.25f;
        RejectByApproach = true;
        AssistStalledWaypoints = true;
        CollectBlockingLoot = true;
    }

    protected override QaPlayerController CreateEpisode(int episodeIndex)
    {
        if (episodeIndex == 0)
        {
            string path = Environment.GetEnvironmentVariable("QA_TRIAL_CONFIG");
            var parameters = JsonUtility.FromJson<Assignment>(File.ReadAllText(path))?.environment?.parameters;
            seedOffset = parameters?.seed_offset ?? 0;
            int actionLimit = parameters?.action_limit ?? 0;
            StallMemoryActions = parameters?.stall_memory_actions ?? 0;
            if (seedOffset < 0 || actionLimit < 0 || StallMemoryActions < 0)
                throw new InvalidOperationException("Caves requires nonnegative seed, action-limit, and stall-memory settings.");
            if (actionLimit > 0) MaxActions = actionLimit;
            ExpireAfterExhaustion = StallMemoryActions > 0;
            UseCombatAndLoot = parameters?.use_combat_and_loot ?? false;
            ExploreKnownFloor = parameters?.explore_known_floor ?? false;
            ConfigurePolicySampling(parameters?.policy_action_std);
        }

        Seed = checked(810000 + seedOffset + episodeIndex);
        var player = BeginCaveEpisode(manager, generator, Seed, out var exit);
        Rooms = manager.GeneratedRoomCount;
        ExitPosition = exit.transform.position;
        return player;
    }

    internal static QaPlayerController BeginCaveEpisode(PcgDungeonManager manager, Generator generator, int seed, out QaExitZone exit)
    {
        if (generator.GenerationSettings.generation.levelStyle != Data.LevelStyle.Caves ||
            !manager.StartPlayerEpisode(seed) || generator.CurrentCaveLayout == null)
            throw new InvalidOperationException("Could not start the authored Caves level for seed " + seed);

        exit = generator.GetComponentInChildren<QaExitZone>();
        if (exit == null) throw new InvalidOperationException("The generated cave has no exit for seed " + seed);
        MarkTarget(exit.gameObject, QaObjectType.LevelEnd);
        // The original lava collider is tagged Wall. Supply the ray label expected by this policy.
        foreach (Transform tile in generator.transform.Find("Cave rock and lava"))
            if (tile.name == "Lava (blocked)" && tile.GetComponent<Collider>().enabled)
                MarkTarget(tile.gameObject, QaObjectType.Wall);

        return manager.ActivePlayer;
    }

    protected override QaUnitReport OnUnitEnd()
    {
        var layout = generator.CurrentCaveLayout;
        var lava = new List<Vector2Int>();
        var bridges = new List<Vector2Int>();
        for (int x = 0; x < layout.Width; x++)
        for (int z = 0; z < layout.Height; z++)
        {
            if (layout.Bridge[x, z]) bridges.Add(new Vector2Int(x, z));
            else if (layout.Lava[x, z]) lava.Add(new Vector2Int(x, z));
        }
        var report = (EpisodeReport)base.OnUnitEnd();
        report.evidence.cave_walkable_tiles = layout.WalkableCount;
        report.evidence.lava_tiles = lava.ToArray();
        report.evidence.bridge_tiles = bridges.ToArray();
        return report;
    }

    protected override void CloseEpisode(string reason) => manager.EndPlayerEpisode(reason);

    private static void MarkTarget(GameObject target, QaObjectType kind)
    {
        if (!target.TryGetComponent(out ModelSensorTarget label))
            label = target.AddComponent<ModelSensorTarget>();
        label.HitId = kind;
    }
}
