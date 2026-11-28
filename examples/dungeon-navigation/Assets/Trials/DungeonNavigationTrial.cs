using System;
using System.Collections.Generic;
using Kcccr.QaFramework;
using UnityEngine;

public abstract class DungeonNavigationTrial : QaPlatformAdapter
{
    protected int Seed, Rooms, MaxActions;
    protected Vector3 ExitPosition;
    protected bool TraceNavigation, RejectByApproach;
    protected int StallMemoryActions;
    protected bool ExpireAfterExhaustion;
    protected float GridCellWidth = ModelSensorGrid.CellSize;
    protected float MinArrivalRadius;
    protected bool PastVisibleDoors, GeometricDoorSteering, AssistStalledWaypoints;
    protected bool CollectBlockingLoot;
    protected Vector2 PolicyActionStd;
    protected CathedralDoorways Doorways;
    protected abstract QaPlayerController CreateEpisode(int episodeIndex);
    protected abstract void CloseEpisode(string reason);

    private QaPlayerController player;
    private CharacterController actor;
    private ModelSensorPackage sensors;
    private QaNavigator4 navigator;
    private Vector3 spawn;
    private bool exitVisibleAtSpawn;
    private int firstExitDiscoveryStep;
    private int unguidedActions;
    private double actionEndsAt;
    private readonly List<MovementSample> movement = new();
    private MovementSample? pendingMovement;
    private DungeonNavigationTrace trace;
    private System.Random policyRandom;

    protected override int ObservationSize => QaWaypointPolicyMapping.ObservationSize;
    protected override int ActionSize => QaWaypointPolicyMapping.ActionSize;

    protected void Update()
    {
        if (CanStartUnit)
            StartUnit(gameObject.scene.name + "-" + (CompletedEpisodes + 1).ToString("D3"));
    }

    protected override void OnUnitStart()
    {
        Time.timeScale = 100f;
        player = CreateEpisode(CompletedEpisodes);
        policyRandom = PolicyActionStd.x > 0f ? new System.Random(Seed) : null;
        actor = player.GetComponent<CharacterController>();
        sensors = player.GetComponent<ModelSensorPackage>();
        spawn = player.transform.position;
        Physics.SyncTransforms();
        sensors.TrackSemanticObjects = true;
        sensors.BeginAttempt(GridCellWidth);
        exitVisibleAtSpawn = sensors.ExitVisible;
        firstExitDiscoveryStep = -1;
        RecordExitDiscovery();
        navigator = new QaNavigator4(RejectByApproach)
        {
            MinArrivalRadius = MinArrivalRadius, AssistStalledWaypoints = AssistStalledWaypoints,
            CollectBlockingLoot = CollectBlockingLoot
        };
        if (GeometricDoorSteering)
            navigator.VisiblePassage = (target, exploring) => Doorways.VisiblePassage(sensors, actor.transform.position, target, exploring);
        else if (PastVisibleDoors) navigator.VisibleDoorwayTarget = () => Doorways.VisibleTarget(sensors, actor.transform.position);
        if (!ExpireAfterExhaustion) navigator.StallMemoryActions = StallMemoryActions;
        if (TraceNavigation)
        {
            trace = new DungeonNavigationTrace(Seed, navigator);
            actor.gameObject.AddComponent<DungeonNavigationContacts>().Trace = trace;
        }
        navigator.Advance(sensors, actor, false);
        unguidedActions = 0;
        movement.Clear();
        pendingMovement = null;
    }

    protected override float[] CollectObservation()
    {
        RecordMovement();
        sensors.Capture();
        RecordExitDiscovery();
        if (player.Metrics.StepCount > 0)
        {
            if (ExpireAfterExhaustion && navigator.Status == "observed_space_exhausted")
                navigator.StallMemoryActions = StallMemoryActions;
            navigator.Advance(sensors, actor, true);
        }
        return QaWaypointPolicyMapping.Observe(player.Observe(), sensors, navigator, player.transform.position);
    }

    protected override void ApplyAction(float[] action)
    {
        var command = QaAction.Noop;
        bool collectedLoot = false;
        if (navigator.SteeringDoor || navigator.AssistingWaypoint)
        {
            collectedLoot = CollectBlockingLoot && player.TryCollectLootInReach(navigator.WaypointLoot);
            if (!collectedLoot) command = navigator.GeometricMovement();
        }
        else if (navigator.HasWaypoint)
        {
            command = QaLocalMovementPolicyMapping.Decode(action);
            if (policyRandom != null)
            {
                // Sample the trained Gaussian from the unclipped actor mean, then clamp in QaAction.
                double radius = Math.Sqrt(-2.0 * Math.Log(1.0 - policyRandom.NextDouble()));
                double angle = 2.0 * Math.PI * policyRandom.NextDouble();
                command = new QaAction(action[0] + PolicyActionStd.x * (float)(radius * Math.Cos(angle)),
                    action[1] + PolicyActionStd.y * (float)(radius * Math.Sin(angle)), false, false, 0);
            }
        }
        else
            unguidedActions++;
        trace?.BeginAction(movement.Count + 1, actor.transform.position, sensors.NavigationTraces);
        player.ApplyAction(command);
        pendingMovement = new MovementSample
        {
            move_x = command.MoveX, move_z = command.MoveY, has_waypoint = navigator.HasWaypoint,
            waypoint = navigator.Waypoint, waypoint_version = navigator.WaypointVersion,
            navigation_status = navigator.Status, waypoint_assisted = navigator.AssistingWaypoint,
            blocking_loot_collected = collectedLoot
        };
        actionEndsAt = Time.fixedTimeAsDouble + QaPlayerController.StepDurationSeconds;
    }

    // Sample post-action position at the next decision or at episode termination.
    private void RecordMovement()
    {
        if (!pendingMovement.HasValue)
            return;

        var sample = pendingMovement.Value;
        sample.position = player.transform.position;
        Doorways?.Track(movement.Count == 0 ? spawn : movement[movement.Count - 1].position, sample.position);
        movement.Add(sample);
        trace?.EndAction(sample);
        pendingMovement = null;
    }

    protected override void FixedUpdate()
    {
        // Let the final action finish its physics interval before declaring a timeout.
        if (player != null && (!player.EpisodeActive || player.Metrics.StepCount >= MaxActions))
        {
            if (!player.EpisodeActive || Time.fixedTimeAsDouble >= actionEndsAt)
                EndUnit();
            return;
        }
        base.FixedUpdate();
    }

    private void RecordExitDiscovery()
    {
        if (firstExitDiscoveryStep < 0 && (sensors.Grid.ExitDiscoveryVersion > 0 || sensors.Memory.ExitVersion > 0))
            firstExitDiscoveryStep = player.Metrics.StepCount;
    }

    protected override QaUnitReport OnUnitEnd()
    {
        RecordMovement();
        var metrics = player.Metrics;
        sensors.EndAttempt();
        RecordExitDiscovery();
        string reason = "timeout";
        if (metrics.Completed)
            reason = "exit";
        else if (metrics.PlayerDied)
            reason = "death";
        var report = new EpisodeReport
        {
            evidence = new EpisodeEvidence
            {
                seed = Seed, room_count = Rooms, action_limit = MaxActions,
                navigation_rays = sensors.NavigationTraces.Length, policy_rays = sensors.Rays.Length,
                grid_cell_size = sensors.Grid.CellWidth, reject_by_approach = navigator.RejectByApproach,
                min_arrival_radius = MinArrivalRadius, past_visible_doors = PastVisibleDoors,
                geometric_door_steering = GeometricDoorSteering,
                policy_action_std = PolicyActionStd,
                assist_stalled_waypoints = AssistStalledWaypoints, waypoint_assist_attempts = navigator.AssistAttempts,
                assisted_arrival_versions = new List<int>(navigator.AssistedArrivals).ToArray(),
                doorway_assignments = navigator.DoorwayAssignments, doorways_crossed = Doorways?.Crossings ?? 0,
                stall_memory_actions = navigator.StallMemoryActions, expire_after_exhaustion = ExpireAfterExhaustion,
                steps = metrics.StepCount, simulated_seconds = metrics.ElapsedSeconds,
                reached_exit = metrics.Completed, end_reason = reason,
                exit_discovered = firstExitDiscoveryStep >= 0,
                first_exit_discovery_step = firstExitDiscoveryStep,
                exit_visible_at_spawn = exitVisibleAtSpawn, unguided_actions = unguidedActions,
                stuck_events = metrics.StuckEvents, waypoint_arrivals = navigator.WaypointArrivals,
                viewed_open_cells = sensors.ViewedOpenCellCount, recovery_attempts = navigator.RecoveryAttempts,
                spawn = spawn, exit = ExitPosition, final_position = player.transform.position,
                movement = movement.ToArray()
            }
        };
        if (trace != null)
        {
            report.evidence.navigation_trace = trace.FileName;
            trace.Dispose();
            trace = null;
        }
        CloseEpisode(reason);
        player = null;
        return report;
    }

    [Serializable] public sealed class EpisodeReport : QaUnitReport { public EpisodeEvidence evidence; }

    protected override void OnDisable()
    {
        trace?.Dispose(); trace = null;
        base.OnDisable();
    }

    [Serializable] public struct EpisodeEvidence
    {
        public int seed, room_count, action_limit, steps;
        public int navigation_rays, policy_rays;
        public float simulated_seconds, grid_cell_size;
        public float min_arrival_radius;
        public bool past_visible_doors, geometric_door_steering;
        public Vector2 policy_action_std;
        public bool assist_stalled_waypoints;
        public int waypoint_assist_attempts;
        public int[] assisted_arrival_versions;
        public int doorway_assignments, doorways_crossed;
        public bool reject_by_approach;
        public int stall_memory_actions;
        public bool expire_after_exhaustion;
        public bool reached_exit, exit_discovered, exit_visible_at_spawn;
        public string end_reason;
        public int unguided_actions, stuck_events, waypoint_arrivals;
        public int first_exit_discovery_step, viewed_open_cells, recovery_attempts;
        public Vector3 spawn, exit, final_position;
        public MovementSample[] movement;
        public string navigation_trace;
    }

    [Serializable] public struct MovementSample
    {
        public Vector3 position, waypoint;
        public float move_x, move_z;
        public bool has_waypoint, waypoint_assisted;
        public bool blocking_loot_collected;
        public int waypoint_version;
        public string navigation_status;
    }
}
