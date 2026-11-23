"""Compare the first and last logged mean rewards for each training node."""

from qa_framework.platform.finalizer import AnalysisContext


def analyze(context: AnalysisContext) -> dict:
    rewards = {run["node_id"]: {} for run in context.records()}
    for record in context.training_metrics():
        reward = record["metrics"].get("rollout/ep_rew_mean")
        if reward is not None:
            node = rewards[record["node_id"]]
            node.setdefault("first_mean_reward", reward)
            node["last_mean_reward"] = reward
    return {"metrics": {"rewards_by_node": rewards}}
