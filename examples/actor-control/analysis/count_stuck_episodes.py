"""Count episodes with less than 0.01 units of motion over their last five decisions."""

from qa_framework.platform.finalizer import AnalysisContext


def analyze(context: AnalysisContext) -> dict:
    counts = {}
    for episode in context.records():
        positions = episode["report"]["evidence"]["positions"][-6:]
        if len(positions) != 6:
            raise ValueError("stuck analysis requires six position samples per episode")
        pair = f"{episode['agent_id']}/{episode['environment_id']}"
        result = counts.setdefault(pair, {"episodes": 0, "stuck_episodes": 0})
        result["episodes"] += 1
        if max(positions) - min(positions) < 0.01:
            result["stuck_episodes"] += 1
    return {"metrics": {"by_agent_and_environment": counts}}
