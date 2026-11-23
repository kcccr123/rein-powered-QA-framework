import json
from pathlib import Path

from qa_framework.platform.config import DAGConfig, NodeConfig
from qa_framework.platform.finalizer import collect_dataset, read_object, read_records, run_analysis, write_json
from qa_framework.platform.runner import RunManager


def save_suite(directory, *, failed=False):
    expected = 2 if failed else 1
    jobs = []
    for agent, positions in (("moving", [0, 1, 2, 3, 4, 5]), ("stalled", [0] * 6)):
        trial_id = f"trial-{agent}"
        trial = directory / "trials" / trial_id
        (trial / "output").mkdir(parents=True)
        row = {"suite_id": "comparison", "trial_id": trial_id, "agent_id": agent,
               "environment_id": "lane", "episode_index": 1,
               "report": {"schema_version": 2, "unit_id": "lane", "attempt_id": trial_id,
                          "evidence": {"positions": positions}}}
        (trial / "output" / "episodes.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
        assignment = {"suite_id": "comparison", "trial_id": trial_id, "agent": {"id": agent},
                      "environment": {"id": "lane"}, "episodes_per_trial": expected}
        jobs.append({"directory": trial.relative_to(directory).as_posix(), "assignment": assignment,
                     "exit_code": 1 if failed else 0, "error": "trial crashed" if failed else None})
    (directory / "jobs.jsonl").write_text("".join(json.dumps(job) + "\n" for job in jobs), encoding="utf-8")
    write_json(directory / "manifest.json", {"node_id": "comparison", "status": "failed" if failed else "succeeded",
                                            "error": "trials crashed" if failed else None, "total_jobs": 2,
                                            "config": {"episodes_per_trial": expected}})


# save two training runs and two trials -> aggregate -> run both example scripts -> check rewards and stuck counts
def test_analysis_reads_training_and_movement_datasets(tmp_path):
    fresh = tmp_path / "fresh"
    legacy = tmp_path / "legacy"
    for source in (fresh, legacy):
        (source / "output").mkdir(parents=True)
        write_json(source / "manifest.json", {"node_id": source.name, "config": {}, "status": "succeeded", "error": None})
        write_json(source / "output" / "artifacts.json", {"parent": None})
    (fresh / "output" / "progress.json").write_text(
        '{"rollout/ep_rew_mean": -1.0}\n{"rollout/ep_rew_mean": 2.0, "train/explained_variance": NaN}\n', encoding="utf-8"
    )
    suite = tmp_path / "comparison"
    save_suite(suite)

    dataset = tmp_path / "dataset"
    index = collect_dataset(dataset, {"training": [fresh, legacy], "testing": [suite]})
    output = tmp_path / "output"
    example = Path(__file__).resolve().parents[1] / "examples" / "actor-control" / "analysis"
    assert run_analysis({"training": [str(example / "compare_rewards.py")],
                         "testing": [str(example / "count_stuck_episodes.py")]}, dataset, tmp_path, output)

    assert index["complete"]
    assert index["training"][1]["metrics_available"] is False
    assert list(read_records(dataset / "training_metrics.jsonl"))[1]["metrics"]["train/explained_variance"] is None
    training, testing = read_object(output / "report.json")["scripts"]
    assert training["metrics"]["rewards_by_node"] == {"fresh": {"first_mean_reward": -1, "last_mean_reward": 2}, "legacy": {}}
    assert testing["metrics"]["by_agent_and_environment"] == {
        "moving/lane": {"episodes": 1, "stuck_episodes": 0}, "stalled/lane": {"episodes": 1, "stuck_episodes": 1}
    }


# leave a suite running -> verify finalizer waits -> crash trials -> keep partial episodes and skip scripts
def test_finalizer_waits_and_skips_incomplete_inputs(tmp_path):
    suite = NodeConfig("comparison", "suite", "trial:local", {}, frozenset())
    finalizer = NodeConfig("review", "finalizer", "analysis:local",
                           {"testing": {"nodes": [suite.id], "scripts": ["/analysis/check.py"]}}, frozenset({suite.id}))
    manager = RunManager(DAGConfig([suite, finalizer], 1, tmp_path, None))
    source = manager.nodes[suite.id]
    review = manager.nodes[finalizer.id]
    review.directory.mkdir(parents=True)
    source.status = "running"
    manager.activate_finalizer(review)
    assert review.status == "pending"

    save_suite(source.directory, failed=True)
    source.status = "failed"
    manager.activate_finalizer(review)

    assert review.status == "blocked"
    assert not manager.ready
    index = read_object(review.directory / "dataset" / "index.json")
    assert index["testing"][0]["expected_episodes"] == 4
    assert index["testing"][0]["received_episodes"] == 2
    assert len(list(read_records(review.directory / "dataset" / "episodes.jsonl"))) == 2
    assert read_object(review.directory / "output" / "report.json")["scripts"][0]["status"] == "skipped"


# return a failed QA check -> verify execution succeeds -> add a crashing script -> verify later checks still run
def test_failed_check_and_script_error_are_distinct(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    write_json(dataset / "index.json", {"complete": True})
    qa = tmp_path / "qa.py"
    qa.write_text('def analyze(context):\n    return {"checks": {"pass": False}}\n', encoding="utf-8")
    broken = tmp_path / "broken.py"
    broken.write_text('def analyze(context):\n    raise ValueError("broken analysis")\n', encoding="utf-8")

    assert run_analysis({"testing": [str(qa)]}, dataset, tmp_path, tmp_path / "qa-only")
    assert not run_analysis({"testing": [str(broken), str(qa)]}, dataset, tmp_path, tmp_path / "both")

    first, second = read_object(tmp_path / "both" / "report.json")["scripts"]
    assert first["status"] == "failed"
    assert first["error"] == "ValueError: broken analysis"
    assert second["status"] == "succeeded"
    assert second["checks"] == {"pass": False}
