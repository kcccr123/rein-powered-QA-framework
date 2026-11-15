using System;
using System.IO;
using UnityEngine;

namespace Kcccr.QaFramework
{
    [Serializable]
    internal sealed class QaTrialAssignment
    {
        public string suite_id;
        public string trial_id;
        public int episodes_per_trial;
        public EnvironmentSettings environment;
        public AgentSettings agent;

        [Serializable] internal sealed class EnvironmentSettings { public string id; public string scene; }
        [Serializable] internal sealed class AgentSettings { public string id; public string model; }

        internal static QaTrialAssignment Read()
        {
            string path = System.Environment.GetEnvironmentVariable("QA_TRIAL_CONFIG");
            if (string.IsNullOrWhiteSpace(path))
                throw new InvalidOperationException("QA_TRIAL_CONFIG is required.");
            var assignment = JsonUtility.FromJson<QaTrialAssignment>(File.ReadAllText(path));
            if (assignment == null || string.IsNullOrWhiteSpace(assignment.suite_id)
                || string.IsNullOrWhiteSpace(assignment.trial_id) || assignment.episodes_per_trial <= 0
                || assignment.environment == null || string.IsNullOrWhiteSpace(assignment.environment.id)
                || string.IsNullOrWhiteSpace(assignment.environment.scene)
                || assignment.agent == null || string.IsNullOrWhiteSpace(assignment.agent.id)
                || string.IsNullOrWhiteSpace(assignment.agent.model))
                throw new InvalidOperationException("Expected trial IDs, episode count, environment.scene, and agent.model.");
            return assignment;
        }
    }
}
