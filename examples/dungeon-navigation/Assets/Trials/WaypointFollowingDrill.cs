using UnityEngine;

public sealed class WaypointFollowingDrill
{
    public const int ActionLimit = 600;
    private const float ProgressReward = 0.05f;

    private System.Random random;
    private ModelSensorPackage sensors;
    private CharacterController actor;
    private float previousDistance;
    private int pauseAt, pauseRemaining, handoffAt, retryAt;

    public bool HasWaypoint { get; private set; }
    public Vector3 Waypoint { get; private set; }
    public int Assignments { get; private set; }
    public int Arrivals { get; private set; }
    public int Handoffs { get; private set; }
    public int Pauses { get; private set; }
    public int PausedActions { get; private set; }
    public int UnassignedActions { get; private set; }

    public void Reset(int seed, CharacterController controller, ModelSensorPackage perception)
    {
        random = new System.Random(seed);
        actor = controller;
        sensors = perception;
        HasWaypoint = false;
        Assignments = Arrivals = Handoffs = Pauses = PausedActions = UnassignedActions = 0;
        pauseAt = random.Next(35, 91);
        handoffAt = random.Next(12, 34);
        pauseRemaining = retryAt = 0;
        TryAssign();
    }

    // Score the completed action before a pause or a handoff changes its target.
    public float Advance(int step)
    {
        bool arrived = false;
        float reward = 0f;
        if (HasWaypoint)
        {
            float distance = QaNavigationCoordinator.Distance(actor.transform.position, Waypoint);
            reward = ProgressReward * (previousDistance - distance);
            previousDistance = distance;
            arrived = distance <= QaNavigationCoordinator.AcceptanceRadius;
            if (arrived)
            {
                Arrivals++;
                reward += 1f;
                HasWaypoint = false;
            }
        }
        else if (pauseRemaining > 0)
        {
            PausedActions++;
            pauseRemaining--;
        }
        else
        {
            UnassignedActions++;
        }

        if (step == ActionLimit || pauseRemaining > 0)
            return reward;

        if (step == pauseAt)
        {
            HasWaypoint = false;
            Pauses++;
            pauseRemaining = random.Next(5, 16);
            pauseAt = step + pauseRemaining + random.Next(35, 91);
        }
        else if (HasWaypoint && step >= handoffAt)
        {
            if (TryAssign())
            {
                Handoffs++;
                handoffAt = step + random.Next(12, 34);
            }
            else
            {
                handoffAt = step + 5;
            }
        }
        else if (!HasWaypoint && step >= retryAt)
        {
            if (!TryAssign())
                retryAt = step + 5;
        }
        return reward;
    }

    private bool TryAssign()
    {
        Vector3 origin = actor.transform.position;
        float heading = (float)random.NextDouble() * Mathf.PI * 2f;
        for (int i = 0; i < 32; i++)
        {
            float angle = heading + i * 2.39996323f;
            float distance = 1.5f + (float)random.NextDouble() * 8f;
            Vector3 target = origin + new Vector3(Mathf.Sin(angle), 0f, Mathf.Cos(angle)) * distance;
            if (TryTarget(origin, target, escape: false))
                return true;
        }

        // Short escape legs allow recovery when the actor starts close to a wall.
        for (int i = 0; i < 24; i++)
        {
            float angle = heading + i * Mathf.PI / 12f;
            Vector3 target = origin + new Vector3(Mathf.Sin(angle), 0f, Mathf.Cos(angle)) * 1.5f;
            if (TryTarget(origin, target, escape: true))
                return true;
        }
        return false;
    }

    private bool TryTarget(Vector3 origin, Vector3 target, bool escape)
    {
        if (HasWaypoint && QaNavigationCoordinator.Distance(target, Waypoint) < 1f)
            return false;
        if (!Physics.Raycast(target + Vector3.up * 0.5f, Vector3.down, out RaycastHit floor, 1.5f,
                Physics.DefaultRaycastLayers, QueryTriggerInteraction.Ignore) || !floor.collider.CompareTag("Floor"))
            return false;
        bool clear = escape ? sensors.CanUseExplorationEscape(target, actor) : sensors.CanUseWaypoint(target, actor);
        if (!clear)
            return false;

        Waypoint = target;
        HasWaypoint = true;
        previousDistance = QaNavigationCoordinator.Distance(origin, target);
        Assignments++;
        return true;
    }
}
