using System.Collections.Generic;
using UnityEngine;

// Cathedral corridors are the single floor tiles connecting two rooms.
public sealed class CathedralDoorways
{
    public sealed class Opening
    {
        public Vector3 Center, Axis;
        public bool Crossed { get; private set; }
        private int crossingDirection;

        public float Along(Vector3 p) => Vector3.Dot(p - Center, Axis);
        public Vector3 FarTarget(int direction) => Center + direction * Axis;
        private float Lateral(Vector3 p) => Mathf.Abs(Vector3.Dot(p - Center, new Vector3(Axis.z, 0f, -Axis.x)));

        private bool IntersectsOpening(Vector3 a, Vector3 b)
        {
            float from = Along(a), to = Along(b);
            if (from == to || from * to > 0f) return false;
            return Lateral(Vector3.Lerp(a, b, from / (from - to))) <= 0.5f;
        }

        public bool Visible(ModelSensorPackage sensors)
        {
            foreach (var ray in sensors.NavigationTraces)
                if (IntersectsOpening(ray.Origin, ray.Endpoint)) return true;
            return false;
        }

        public bool LeadsThrough(Vector3 from, Vector3 target) =>
            Mathf.Abs(Along(target)) > 0.5f && IntersectsOpening(from, target);

        public bool Track(Vector3 from, Vector3 to, int requiredDirection = 0)
        {
            if (Crossed) return false;
            if (IntersectsOpening(from, to))
                crossingDirection = Along(to) > Along(from) ? 1 : -1;
            if (requiredDirection != 0 && requiredDirection != crossingDirection) return false;
            if (crossingDirection * Along(to) <= 0.5f || Lateral(to) > 0.5f) return false;
            Crossed = true;
            return true;
        }
    }

    public readonly List<Opening> Openings = new();
    public int Crossings { get; private set; }

    public CathedralDoorways(Generator generator)
    {
        foreach (Room room in generator.GetComponentsInChildren<Room>())
        {
            float firstX = room.transform.GetChild(0).position.x;
            foreach (Transform child in room.transform)
            {
                if (!child.name.StartsWith("Corridor ") || child.childCount == 0) continue;
                var center = child.GetChild(0).position;
                bool alongX = center.x < firstX || center.x >= firstX + room.GeneratedSize.x;
                Openings.Add(new Opening { Center = center, Axis = alongX ? Vector3.right : Vector3.forward });
            }
        }
    }

    public void Track(Vector3 before, Vector3 after)
    {
        foreach (var door in Openings)
            if (door.Track(before, after)) Crossings++;
    }

    public Vector3? VisibleTarget(ModelSensorPackage sensors, Vector3 player)
    {
        return VisiblePassage(sensors, player, null, true)?.far;
    }

    public (Vector3 near, Vector3 far)? VisiblePassage(ModelSensorPackage sensors, Vector3 player,
        Vector3? routeTarget, bool exploring)
    {
        Opening chosen = null;
        float best = 4f;
        foreach (var door in Openings)
        {
            float distance = QaNavigationCoordinator.Distance(player, door.Center);
            if (distance >= best || Mathf.Abs(door.Along(player)) < 0.5f) continue;
            // Re-use a crossed opening only when the current route actually leads back through it.
            if ((door.Crossed || !exploring) &&
                !(routeTarget.HasValue && door.LeadsThrough(player, routeTarget.Value))) continue;
            if (!door.Visible(sensors)) continue;
            best = distance;
            chosen = door;
        }
        if (chosen == null) return null;
        int direction = chosen.Along(player) < 0f ? 1 : -1;
        return (chosen.FarTarget(-direction), chosen.FarTarget(direction));
    }
}
