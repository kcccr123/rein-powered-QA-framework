using System;
using Kcccr.QaFramework;
using Kcccr.UnityReinforcementLearning;
using UnityEngine;

[RequireComponent(typeof(RlTrainingHost), typeof(CathedralTrainingEnvironment))]
public sealed class CathedralTraining : QaTrainingAdapter
{
    [Serializable] private sealed class Assignment { public EnvironmentSettings environment; }
    [Serializable] private sealed class EnvironmentSettings { public Parameters parameters; }
    [Serializable] private sealed class Parameters
    {
        public bool waypoint_following, full_navigation;
        public string movement_drill;
    }

    public override int ConfigureAndStart(string assignmentJson)
    {
        var host = GetComponent<RlTrainingHost>();
        var assignment = JsonUtility.FromJson<Assignment>(assignmentJson);
        var parameters = assignment?.environment?.parameters;
        GetComponent<CathedralTrainingEnvironment>().Prepare(parameters?.waypoint_following == true,
            parameters?.movement_drill, parameters?.full_navigation == true);
        if (!host.StartTraining())
            throw new InvalidOperationException("The cathedral training host could not start.");
        return host.Port;
    }
}
