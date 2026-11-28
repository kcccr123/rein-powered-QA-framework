using UnityEngine;

public sealed class MixedRoomsTrial : DungeonNavigationTrial
{
    [SerializeField] private GameObject playerPrefab;
    private GameObject episodeRoot;

    protected override QaPlayerController CreateEpisode(int episodeIndex)
    {
        Rooms = 1 + episodeIndex % 6;
        int group = episodeIndex / 6;
        // QA-84 selected one-room seeds via seed % 3. QA-85 cycled two to six via seed % 5.
        Seed = Rooms == 1 ? 481002 + 3 * group : 766000 + 5 * group + Rooms - 2;
        MaxActions = Rooms == 1 ? 600 : 1800;

        episodeRoot = new GameObject("Mixed rooms " + Seed);
        var generator = episodeRoot.AddComponent<Generator>();
        var rooms = new Room[Rooms];
        for (int i = 0; i < rooms.Length; i++)
        {
            var room = new GameObject("Room " + (i + 1));
            room.transform.SetParent(episodeRoot.transform, false);
            rooms[i] = room.AddComponent<Room>();
        }

        var exit = new GameObject("QaExitZone");
        exit.transform.SetParent(rooms[Rooms - 1].transform, false);
        exit.AddComponent<BoxCollider>().size = new Vector3(2f, 3f, 2f);
        exit.AddComponent<QaExitZone>();
        exit.AddComponent<ModelSensorTarget>().HitId = QaObjectType.LevelEnd;

        var instance = Instantiate(playerPrefab, episodeRoot.transform);
        var player = instance.GetComponent<QaPlayerController>() ?? instance.AddComponent<QaPlayerController>();
        if (!instance.TryGetComponent<ModelSensorPackage>(out _))
            instance.AddComponent<ModelSensorPackage>();
        var layout = new QaMixedRoomLayout();
        layout.Build(generator, rooms, player, Seed, Rooms > 1);
        var controller = instance.GetComponent<CharacterController>();
        controller.enabled = false;
        instance.transform.position = layout.Spawn + Vector3.up * 0.11f;
        controller.enabled = true;
        player.BeginTest();
        ExitPosition = layout.Exit;
        return player;
    }

    protected override void CloseEpisode(string reason)
    {
        episodeRoot.SetActive(false);
        Destroy(episodeRoot);
    }
}
