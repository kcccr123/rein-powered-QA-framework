using System;
using Kcccr.QaFramework;
using Kcccr.UnityReinforcementLearning;
using UnityEngine;

public sealed class SampleTrainingAdapter : QaTrainingAdapter
{
    [SerializeField] private SampleActor actor;
    [SerializeField] private SampleTrainingEnvironment environment;
    [SerializeField] private RlTrainingHost host;

    [Serializable] private sealed class Assignment { public EnvironmentSettings environment; }
    [Serializable] private sealed class EnvironmentSettings { public Parameters parameters; }
    [Serializable] private sealed class Parameters
    {
        public float step_distance = 0.25f;
        public float target_direction = 1f;
    }

    public void Configure(SampleActor controlledActor, SampleTrainingEnvironment trainingEnvironment, RlTrainingHost trainingHost)
    {
        actor = controlledActor;
        environment = trainingEnvironment;
        host = trainingHost;
    }

    public override int ConfigureAndStart(string assignmentJson)
    {
        var settings = JsonUtility.FromJson<Assignment>(assignmentJson).environment.parameters;
        if (settings == null || !(settings.step_distance > 0) || float.IsInfinity(settings.step_distance)
            || (settings.target_direction != 1f && settings.target_direction != -1f))
            throw new InvalidOperationException("Training requires positive step_distance and target_direction of -1 or 1.");
        actor.Configure(settings.step_distance);
        environment.Configure(actor, settings.target_direction);
        if (!host.StartTraining())
            throw new InvalidOperationException("RL training host could not start.");
        return host.Port;
    }
}
