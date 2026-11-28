using System;
using UnityEngine;

// Focused 0.1-second actions using the same actor and waypoint observation as a Cathedral trial.
public sealed class CathedralMovementDrill
{
    public const int ActionLimit = 64;
    public bool IsDoor { get; }
    public Vector3 Target { get; private set; }
    public int Assignments { get; private set; }
    public bool HasTarget { get; private set; }
    public int Stops { get; private set; }
    public int Overshoots { get; private set; }
    public bool Crossed { get; private set; }

    private readonly CharacterController actor;
    private readonly ModelSensorPackage sensors;
    private readonly System.Random random;
    private readonly QaWaypointClearance clearance = new();
    private CathedralDoorways.Opening door;
    private Vector3 previous;
    private float radius;
    private int direction;

    public CathedralMovementDrill(int seed, CharacterController actor, ModelSensorPackage sensors, Generator generator, bool isDoor)
    {
        this.actor = actor;
        this.sensors = sensors;
        random = new System.Random(seed);
        IsDoor = isDoor;
        if (isDoor) PlaceAtDoor(new CathedralDoorways(generator));
        else AssignStop();
        previous = actor.transform.position;
    }

    private float Range(float low, float high) => low + (high - low) * (float)random.NextDouble();

    private void PlaceAtDoor(CathedralDoorways layout)
    {
        var y = actor.transform.position.y;
        // Vary approach side, distance and offset from the centerline. Use actual room floor.
        for (int attempt = 0; attempt < 64; attempt++)
        {
            door = layout.Openings[random.Next(layout.Openings.Count)];
            direction = random.Next(2) == 0 ? -1 : 1;
            var side = new Vector3(door.Axis.z, 0f, -door.Axis.x);
            var start = door.Center - direction * door.Axis * Range(0.9f, 2.4f) + side * Range(-0.85f, 0.85f);
            start.y = y;
            if (!Physics.Raycast(start + Vector3.up * 0.6f, Vector3.down, out var hit, 1.6f,
                    Physics.DefaultRaycastLayers, QueryTriggerInteraction.Ignore) || !hit.collider.CompareTag("Floor")) continue;
            actor.enabled = false;
            actor.transform.position = start;
            actor.enabled = true;
            Physics.SyncTransforms();
            sensors.BeginAttempt();
            if (!sensors.CanOccupyObservedPoint(start, new QaNavigationCapsule(actor)) || !door.Visible(sensors)) continue;
            Target = door.FarTarget(direction);
            HasTarget = true;
            Assignments++;
            return;
        }
        throw new InvalidOperationException("No visible Cathedral doorway with a clear training start.");
    }

    private void AssignStop()
    {
        var position = actor.transform.position;
        float angle = Range(0f, 2f * Mathf.PI);
        clearance.Begin(sensors.Grid, actor);
        for (int i = 0; i < 48; i++)
        {
            angle += 2.39996323f;
            float distance = Range(0.22f, i < 8 && random.Next(4) == 0 ? 2.4f : 0.9f);
            var request = position + distance * new Vector3(Mathf.Cos(angle), 0f, Mathf.Sin(angle));
            if (!clearance.TryLeg(request, true, out var target)) continue;
            distance = QaNavigationCoordinator.Distance(position, target);
            if (distance < 0.18f) continue;
            Target = target;
            HasTarget = true;
            radius = QaNavigator4.ArrivalRadius(distance);
            Assignments++;
            return;
        }
        HasTarget = false;
    }

    public float Score(QaAction command, bool lastAction)
    {
        if (!HasTarget) return 0f;
        Vector3 position = actor.transform.position;
        Vector3 before = Target - previous, after = Target - position;
        before.y = after.y = 0f;
        float remaining = after.magnitude;
        float reward = before.magnitude - remaining - 0.01f;
        if (IsDoor)
        {
            Crossed = door.Track(previous, position, direction);
            if (Crossed) reward += 4f;
        }
        else
        {
            float overshoot = -Vector3.Dot(after, before.normalized);
            if (overshoot > 0.02f)
            {
                Overshoots++;
                reward -= 0.2f + overshoot;
            }
            // A short final command can settle at the target. Holding against a wall at full input cannot.
            float input = new Vector2(command.MoveX, command.MoveY).magnitude;
            if (remaining <= radius && overshoot <= 0.02f && input <= 0.25f)
            {
                Stops++;
                reward += 2f;
                if (!lastAction) AssignStop();
            }
            else if (before.magnitude < 0.4f)
                reward -= 0.2f * Mathf.Max(0f, Mathf.Min(1f, input) - before.magnitude / 0.4f);
        }
        previous = position;
        return reward;
    }
}
