using System;
using UnityEngine;

public sealed class CathedralDungeon
{
    public const int ActionLimit = 1800;

    private readonly PcgDungeonManager manager;
    private readonly Generator generator;

    public int Rooms => manager.GeneratedRoomCount;
    public Vector3 ExitPosition { get; private set; }

    public CathedralDungeon(PcgDungeonManager manager, Generator generator, CathedralSettings settings)
    {
        this.manager = manager;
        this.generator = generator;
        manager.RemoteControlActive = true;
        generator.GenerationSettings.generation.cathedral = settings;
    }

    public QaPlayerController BeginEpisode(int seed)
    {
        Data level = generator.GenerationSettings.generation;
        if (level.dungeonType != DungeonType.Cathedral || level.cathedral == null)
            throw new InvalidOperationException("The cathedral requires its authored settings.");
        if (!manager.StartPlayerEpisode(seed))
            throw new InvalidOperationException("Cathedral generation failed for seed " + seed);

        // The original cathedral assets predate the trained policy's ray labels.
        var exit = generator.GetComponentInChildren<QaExitZone>();
        if (exit == null)
            throw new InvalidOperationException("The generated cathedral has no exit for seed " + seed);
        MarkTarget(exit.gameObject, QaObjectType.LevelEnd);
        foreach (Room room in generator.GetComponentsInChildren<Room>())
            foreach (Transform feature in room.transform)
                if (feature.name == "Column" || feature.name == "Arch")
                    MarkTarget(feature.gameObject, QaObjectType.Wall);

        ExitPosition = exit.transform.position;
        return manager.ActivePlayer;
    }

    public void EndEpisode(string reason)
    {
        manager.EndPlayerEpisode(reason);
    }

    private static void MarkTarget(GameObject target, QaObjectType kind)
    {
        if (!target.TryGetComponent(out ModelSensorTarget label))
            label = target.AddComponent<ModelSensorTarget>();
        label.HitId = kind;
    }
}
