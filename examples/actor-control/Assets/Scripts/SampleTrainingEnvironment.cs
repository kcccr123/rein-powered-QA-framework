using Kcccr.UnityReinforcementLearning;
using UnityEngine;

public sealed class SampleTrainingEnvironment : RlEnvironmentBehaviour
{
    [SerializeField] private SampleActor actor;
    private float targetDirection = 1f;
    public override int ObservationSize => 2;
    public override int ActionSize => 1;

    public void Configure(SampleActor controlledActor, float direction)
    {
        actor = controlledActor;
        targetDirection = direction;
    }

    public override void ResetEpisode(int seed) { actor.ResetEpisode(); }
    public override float[] CollectObservation() { return new[] { actor.Position, 1f }; }
    public override void ApplyAction(float[] action) { actor.Move(action[0]); }
    public override bool IsActionRunning() { return false; }
    public override float CalculateReward(out bool isDone)
    {
        isDone = actor.Decisions >= 10;
        return actor.LastAction * targetDirection;
    }
}
