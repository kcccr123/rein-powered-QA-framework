using System;
using System.Collections.Generic;
using Kcccr.QaFramework;
using UnityEngine;

public sealed class SampleAdapter : QaPlatformAdapter
{
    [SerializeField] private SampleActor actor;
    private readonly List<float> positions = new();
    protected override int ObservationSize => 2;
    protected override int ActionSize => 1;

    public void Configure(SampleActor controlledActor) { actor = controlledActor; }

    private void Awake()
    {
        actor.EpisodeEnded += EndUnit;
        OnReady.AddListener(BeginEpisode);
    }

    private void Update()
    {
        if (CanStartUnit)
            BeginEpisode();
    }

    private void BeginEpisode() { StartUnit("ten-decisions"); }
    protected override void OnUnitStart()
    {
        actor.ResetEpisode();
        positions.Clear();
    }
    protected override float[] CollectObservation() { return new[] { actor.Position, 1f }; }
    protected override void ApplyAction(float[] actions)
    {
        positions.Add(actor.Position);
        actor.Move(actions[0]);
    }

    protected override QaUnitReport OnUnitEnd()
    {
        positions.Add(actor.Position);
        return new ActorReport
        {
            evidence = new ActorEvidence
            {
                scene = gameObject.scene.name,
                decisions = actor.Decisions,
                position = actor.Position,
                positions = positions.ToArray(),
                last_action = actor.LastAction
            }
        };
    }

    [Serializable] public sealed class ActorReport : QaUnitReport { public ActorEvidence evidence; }
    [Serializable] public struct ActorEvidence
    {
        public string scene;
        public int decisions;
        public float position;
        public float[] positions;
        public float last_action;
    }
}
