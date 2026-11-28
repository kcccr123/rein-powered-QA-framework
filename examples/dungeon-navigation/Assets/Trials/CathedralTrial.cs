using System;
using System.IO;
using UnityEngine;

public sealed class CathedralTrial : DungeonNavigationTrial
{
    [SerializeField] private PcgDungeonManager manager;
    [SerializeField] private Generator generator;
    [SerializeField] private CathedralSettings cathedralSettings;
    private CathedralDungeon dungeon;
    private int seedOffset;

    [Serializable] private sealed class TrialAssignment { public TrialEnvironment environment; }
    [Serializable] private sealed class TrialEnvironment { public TrialParameters parameters; }
    [Serializable] private sealed class TrialParameters
    {
        public int action_limit, seed_offset, stall_memory_actions;
        public bool trace_navigation, reject_by_approach, expire_after_exhaustion, past_visible_doors, geometric_door_steering;
        public bool assist_stalled_waypoints;
        public bool collect_blocking_loot;
        public float grid_cell_size, min_arrival_radius;
        public float[] policy_action_std;
    }

    private void Awake()
    {
        dungeon = new CathedralDungeon(manager, generator, cathedralSettings);
        MaxActions = CathedralDungeon.ActionLimit;
    }

    protected override QaPlayerController CreateEpisode(int episodeIndex)
    {
        if (episodeIndex == 0)
        {
            string path = Environment.GetEnvironmentVariable("QA_TRIAL_CONFIG");
            var assignment = JsonUtility.FromJson<TrialAssignment>(File.ReadAllText(path));
            var parameters = assignment?.environment?.parameters;
            int actionLimit = parameters?.action_limit ?? 0;
            seedOffset = parameters?.seed_offset ?? 0;
            TraceNavigation = parameters?.trace_navigation ?? false;
            MinArrivalRadius = parameters?.min_arrival_radius ?? 0f;
            PastVisibleDoors = parameters?.past_visible_doors ?? false;
            GeometricDoorSteering = parameters?.geometric_door_steering ?? false;
            AssistStalledWaypoints = parameters?.assist_stalled_waypoints ?? false;
            CollectBlockingLoot = parameters?.collect_blocking_loot ?? false;
            ConfigurePolicySampling(parameters?.policy_action_std);
            if (MinArrivalRadius < 0f)
                throw new InvalidOperationException("Cathedral min_arrival_radius must be nonnegative.");
            RejectByApproach = parameters?.reject_by_approach ?? false;
            StallMemoryActions = parameters?.stall_memory_actions ?? 0;
            ExpireAfterExhaustion = parameters?.expire_after_exhaustion ?? false;
            if (StallMemoryActions < 0)
                throw new InvalidOperationException("Cathedral stall_memory_actions must be nonnegative.");
            if (parameters != null && parameters.grid_cell_size != 0f) GridCellWidth = parameters.grid_cell_size;
            if (seedOffset < 0)
                throw new InvalidOperationException("Cathedral seed_offset must be nonnegative.");
            if (actionLimit < 0)
                throw new InvalidOperationException("Cathedral action_limit must be positive when supplied.");
            if (actionLimit > 0)
                MaxActions = actionLimit;
        }
        Seed = checked(768000 + seedOffset + episodeIndex);
        var player = dungeon.BeginEpisode(Seed);
        Doorways = new CathedralDoorways(generator);
        Rooms = dungeon.Rooms;
        ExitPosition = dungeon.ExitPosition;
        return player;
    }

    protected override void CloseEpisode(string reason)
    {
        dungeon.EndEpisode(reason);
    }
}
