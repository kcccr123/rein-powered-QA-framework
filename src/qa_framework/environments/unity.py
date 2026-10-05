"""Extension point for a future UnityQAEnvironment adapter.

A future implementation will translate transport-specific actions and observations
to the QAEnvironment contract. An initial transport may use
mlagents_envs.UnityEnvironment; that dependency and all Unity communication are
intentionally deferred. External trainers will continue to use QAEnvironment.
"""
