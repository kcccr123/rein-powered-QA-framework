using System;
using System.Collections.Generic;
using System.IO;
using Kcccr.UnityReinforcementLearning;
using UnityEngine;

public sealed class CathedralTrainingEnvironment : RlEnvironmentBehaviour
{
    [SerializeField] private PcgDungeonManager manager;
    [SerializeField] private Generator generator;
    [SerializeField] private CathedralSettings cathedralSettings;

    private readonly QaWaypointRewardTracker rewards = new();
    private CathedralDungeon dungeon;
    private QaPlayerController player;
    private CharacterController actor;
    private ModelSensorPackage sensors;
    private QaNavigator4 navigator;
    private WaypointFollowingDrill drill;
    private string focusedMode;
    private CathedralMovementDrill focused;
    private QaAction command;
    private string episodesPath;
    private int seed;
    private int episodes;
    private float episodeReward;
    private double actionEndsAt;
    private bool fullNavigation, actorSteered;
    private CathedralDoorways doorways;
    private readonly List<RectInt> roomFloors = new();
    private readonly HashSet<int> visitedRooms = new();
    private Vector3 beforeAction;
    private QaAction referenceAction;

    public override int ObservationSize => QaWaypointPolicyMapping.ObservationSize;
    public override int ActionSize => QaWaypointPolicyMapping.ActionSize;

    public void Prepare(bool waypointFollowing, string movementDrill, bool fullNavigation)
    {
        this.fullNavigation = fullNavigation;
        dungeon = new CathedralDungeon(manager, generator, cathedralSettings);
        focusedMode = string.IsNullOrEmpty(movementDrill) ? null : movementDrill;
        if (focusedMode != null && (waypointFollowing || focusedMode != "hard_stop" && focusedMode != "doorway"))
            throw new ArgumentException("Select one supported Cathedral movement drill.");
        if (fullNavigation && (waypointFollowing || focusedMode != null))
            throw new ArgumentException("Full navigation uses the Cathedral level without a movement drill.");
        if (waypointFollowing) drill = new WaypointFollowingDrill();
        if (waypointFollowing || focusedMode != null)
        {
            Data level = generator.GenerationSettings.generation;
            level.enemies.Clear();
            level.exitRequiresInteract = true;
        }
        episodesPath = Path.Combine(System.Environment.GetEnvironmentVariable("QA_OUTPUT_DIR"), "episodes.jsonl");
        // Run only the action's physics interval while the external trainer waits for a reply.
        Time.maximumDeltaTime = QaPlayerController.StepDurationSeconds;
        Time.timeScale = 0f;
        QualitySettings.vSyncCount = 0;
        Application.targetFrameRate = -1;
    }

    public override void ResetEpisode(int episodeSeed)
    {
        seed = episodeSeed;
        player = dungeon.BeginEpisode(seed);
        actor = player.GetComponent<CharacterController>();
        sensors = player.GetComponent<ModelSensorPackage>();
        Physics.SyncTransforms();
        sensors.TrackSemanticObjects = true;
        if (fullNavigation) sensors.BeginAttempt(0.25f);
        else sensors.BeginAttempt();
        episodeReward = 0f;
        if (focusedMode != null)
        {
            focused = new CathedralMovementDrill(seed, actor, sensors, generator, focusedMode == "doorway");
            return;
        }
        if (drill != null)
        {
            drill.Reset(seed, actor, sensors);
            return;
        }
        navigator = new QaNavigator4(fullNavigation);
        if (fullNavigation)
        {
            doorways = new CathedralDoorways(generator);
            navigator.AssistStalledWaypoints = true;
            navigator.CollectBlockingLoot = true;
            navigator.VisiblePassage = (target, exploring) => doorways.VisiblePassage(sensors, actor.transform.position, target, exploring);
            visitedRooms.Clear();
            roomFloors.Clear();
            foreach (Room room in generator.GetComponentsInChildren<Room>())
            {
                Vector3 first = room.transform.GetChild(0).position;
                roomFloors.Add(new RectInt((int)first.x, (int)first.z, room.GeneratedSize.x, room.GeneratedSize.y));
            }
            VisitRoom(actor.transform.position);
        }
        navigator.Advance(sensors, actor, false);
        rewards.Reset();
        rewards.Assign(navigator.HasWaypoint, navigator.WaypointVersion, navigator.Waypoint, actor.transform.position);
    }

    public override float[] CollectObservation()
    {
        if (focused != null)
            return QaWaypointPolicyMapping.Observe(player.Observe(), sensors, focused.HasTarget, focused.Target, actor.transform.position);
        if (drill != null)
            return QaWaypointPolicyMapping.Observe(player.Observe(), sensors, drill.HasWaypoint, drill.Waypoint, actor.transform.position);
        return QaWaypointPolicyMapping.Observe(player.Observe(), sensors, navigator, actor.transform.position);
    }

    public override void ApplyAction(float[] action)
    {
        command = QaAction.Noop;
        bool hasWaypoint = focused != null ? focused.HasTarget : drill != null ? drill.HasWaypoint : navigator.HasWaypoint;
        beforeAction = actor.transform.position;
        actorSteered = false;
        if (fullNavigation && (navigator.SteeringDoor || navigator.AssistingWaypoint))
        {
            if (!player.TryCollectLootInReach(navigator.WaypointLoot))
                command = navigator.GeometricMovement();
        }
        else if (hasWaypoint)
        {
            command = QaLocalMovementPolicyMapping.Decode(action);
            if (fullNavigation)
            {
                actorSteered = true;
                referenceAction = navigator.GeometricMovement();
            }
        }

        player.ApplyAction(command);
        actionEndsAt = Time.fixedTimeAsDouble + QaPlayerController.StepDurationSeconds;
        Time.timeScale = 100f;
    }

    public override bool IsActionRunning()
    {
        return Time.fixedTimeAsDouble + 0.000001 < actionEndsAt;
    }

    public override float CalculateReward(out bool isDone)
    {
        Time.timeScale = 0f;
        var metrics = player.Metrics;
        if (focused != null)
            return ScoreMovementDrill(metrics, out isDone);
        if (drill != null)
            return ScoreWaypointDrill(metrics, out isDone);
        int actionLimit = fullNavigation ? 3600 : CathedralDungeon.ActionLimit;
        isDone = !player.EpisodeActive || metrics.StepCount >= actionLimit;
        var position = actor.transform.position;

        // Score movement against the waypoint the actor received for this action.
        float reward = fullNavigation ? ScoreFullNavigation(position) : rewards.Measure(position) - 0.001f;
        if (metrics.Completed)
            reward += 50f;
        episodeReward += reward;

        if (isDone)
        {
            string reason = "timeout";
            if (metrics.Completed)
                reason = "exit";
            else if (metrics.PlayerDied)
                reason = "death";
            dungeon.EndEpisode(reason);
            var record = new Episode
            {
                episode = ++episodes, seed = seed, steps = metrics.StepCount, room_count = dungeon.Rooms,
                reached_exit = metrics.Completed,
                exit_discovered = sensors.Grid.ExitDiscoveryVersion > 0 || sensors.Memory.ExitVersion > 0,
                end_reason = reason, reward = episodeReward, waypoint_reward = rewards.PositiveRewardTotal,
                rooms_visited = visitedRooms.Count, waypoint_arrivals = navigator.WaypointArrivals,
                recovery_assists = navigator.AssistAttempts
            };
            File.AppendAllText(episodesPath, JsonUtility.ToJson(record) + "\n");
        }
        else
        {
            sensors.Capture();
            navigator.Advance(sensors, actor, true);
            rewards.Assign(navigator.HasWaypoint, navigator.WaypointVersion, navigator.Waypoint, position);
        }
        return reward;
    }

    private float ScoreFullNavigation(Vector3 position)
    {
        doorways.Track(beforeAction, position);
        float reward = VisitRoom(position) ? 3f : 0f;
        if (actorSteered)
        {
            float dx = command.MoveX - referenceAction.MoveX;
            float dz = command.MoveY - referenceAction.MoveY;
            float progress = QaNavigationCoordinator.Distance(beforeAction, navigator.Waypoint) -
                QaNavigationCoordinator.Distance(position, navigator.Waypoint);
            reward += 0.10f * progress - 0.10f * (dx * dx + dz * dz);
        }
        return reward - 0.002f;
    }

    private bool VisitRoom(Vector3 position)
    {
        var tile = new Vector2Int(Mathf.FloorToInt(position.x + 0.5f), Mathf.FloorToInt(position.z + 0.5f));
        for (int i = 0; i < roomFloors.Count; i++)
            if (roomFloors[i].Contains(tile)) return visitedRooms.Add(i);
        return false;
    }

    private float ScoreMovementDrill(QaEpisodeMetrics metrics, out bool isDone)
    {
        bool last = metrics.StepCount >= CathedralMovementDrill.ActionLimit || !player.EpisodeActive;
        sensors.Capture();
        float reward = focused.Score(command, last);
        episodeReward += reward;
        isDone = last || focused.Crossed || !focused.HasTarget;
        if (isDone)
        {
            string reason = focused.Crossed ? "door_crossed" : !focused.HasTarget ? "no_clear_target" :
                metrics.PlayerDied ? "death" : "time_budget";
            dungeon.EndEpisode(reason);
            var record = new MovementEpisode
            {
                episode = ++episodes, seed = seed, steps = metrics.StepCount, reward = episodeReward,
                drill = focusedMode, end_reason = reason, waypoints_assigned = focused.Assignments,
                hard_stops = focused.Stops, overshoots = focused.Overshoots, door_crossed = focused.Crossed
            };
            File.AppendAllText(episodesPath, JsonUtility.ToJson(record) + "\n");
        }
        return reward;
    }

    [Serializable] private sealed class MovementEpisode
    {
        public int episode, seed, steps, waypoints_assigned, hard_stops, overshoots;
        public float reward;
        public string drill, end_reason;
        public bool door_crossed;
    }

    private float ScoreWaypointDrill(QaEpisodeMetrics metrics, out bool isDone)
    {
        if (!player.EpisodeActive)
            throw new InvalidOperationException("The waypoint drill player stopped before the time limit.");

        float reward = drill.Advance(metrics.StepCount);
        episodeReward += reward;
        isDone = metrics.StepCount >= WaypointFollowingDrill.ActionLimit;
        if (isDone)
        {
            dungeon.EndEpisode("time_budget");
            var record = new WaypointEpisode
            {
                episode = ++episodes, seed = seed, steps = metrics.StepCount,
                simulated_seconds = metrics.ElapsedSeconds, reward = episodeReward,
                waypoints_assigned = drill.Assignments, waypoints_reached = drill.Arrivals,
                random_handoffs = drill.Handoffs, pauses = drill.Pauses,
                paused_actions = drill.PausedActions, unassigned_actions = drill.UnassignedActions
            };
            File.AppendAllText(episodesPath, JsonUtility.ToJson(record) + "\n");
        }
        else
        {
            sensors.Capture();
        }
        return reward;
    }

    [Serializable]
    private sealed class WaypointEpisode
    {
        public int episode, seed, steps;
        public float simulated_seconds, reward;
        public int waypoints_assigned, waypoints_reached, random_handoffs, pauses, paused_actions, unassigned_actions;
    }

    [Serializable]
    private sealed class Episode
    {
        public int episode, seed, steps, room_count, rooms_visited, waypoint_arrivals, recovery_assists;
        public bool reached_exit, exit_discovered;
        public string end_reason;
        public float reward, waypoint_reward;
    }
}
