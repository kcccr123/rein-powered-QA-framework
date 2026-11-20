using UnityEngine;

namespace Kcccr.QaFramework
{
    /// <summary>Applies game-owned parameters and starts the Unity RL training host.</summary>
    public abstract class QaTrainingAdapter : MonoBehaviour
    {
        /// <summary>
        /// Parse environment.parameters from the assignment, configure the environment,
        /// then start RlTrainingHost with autoStart disabled. Return its listening port.
        /// Throw if setup fails. Called after scene Awake/OnEnable, before Start.
        /// </summary>
        public abstract int ConfigureAndStart(string assignmentJson);
    }
}
