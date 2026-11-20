# Actor training and evaluation sample

This sample builds two Linux Players: evaluation uses ShortLane (0.25 units per decision) and LongLane (0.5 units); training uses TrainingLane with YAML-selected step distance and target direction. Both use two observations (position, constant 1) and one continuous action. Episodes end after ten decisions.

`SampleActor` owns movement; `SampleAdapter` maps evaluation observations/actions/evidence. `SampleTrainingEnvironment` supplies the Unity RL callbacks and rewards movement toward the requested direction. `SampleTrainingAdapter` applies parameters before starting the RL host. The sample is deterministic and does not use reset seeds.

## Build the Players

Install `qa-framework[platform,test]` on the host and Linux Build Support (Mono) for Unity 6000.3.4f1. The project pins Inference Engine 2.4.1 and Unity RL commit `b8ff299e91009311459c7b83f25f0f036d5df740`.

From the framework root on Windows, run these commands separately, waiting for each Editor process to exit successfully before starting the next:

```powershell
& 'C:/Program Files/Unity/Hub/Editor/6000.3.4f1/Editor/Unity.exe' -batchmode -nographics -quit -projectPath "$PWD/examples/actor-control" -executeMethod BuildSample.Build -logFile "$PWD/build/trial-build.log"
& 'C:/Program Files/Unity/Hub/Editor/6000.3.4f1/Editor/Unity.exe' -batchmode -nographics -quit -projectPath "$PWD/examples/actor-control" -executeMethod BuildSample.BuildTraining -logFile "$PWD/build/training-build.log"
```

Create the log directory first if needed: `New-Item -ItemType Directory -Force build`. The build methods generate their scenes with the appropriate bootstrap first. Outputs are `Build/Linux/ActorSample.x86_64` and `Build/Training/ActorTraining.x86_64`. Optional output overrides are `QA_SAMPLE_BUILD_PATH` and `QA_TRAINING_BUILD_PATH`.

The sample strips compute shaders and keeps App UI editor-only for CPU inference. Both images set `SDL_VIDEODRIVER=dummy` for headless native input initialization.

## Build images and run

Supply the existing Unity RL checkout at the pinned revision through a Docker named build context. The training Dockerfile packages its existing Python trainer/exporter without copying library source into this repository.

```powershell
docker build -f examples/actor-control/Dockerfile -t qa-actor-sample:local .
docker build --build-context unity-rl=C:/path/to/unity-reinforcement-learning -f examples/actor-control/Dockerfile.training -t qa-actor-training:local .
python -m qa_framework.platform.cli examples/actor-control/training.yaml
```

Update `model_preparation.unity_editor` for your machine. Training runs 128 steps from scratch, branches that checkpoint into running/right and walking/left stages of another 128 steps each, then evaluates all three models in both lanes. The shared limit is two containers. Expect three training outputs, six trial outputs and eighteen episode records. These small budgets demonstrate the lifecycle; they do not establish policy quality.

To verify evaluation alone with the included constant-action models:

```powershell
python -m qa_framework.platform.cli examples/actor-control/suite.yaml
```

This produces four trials and twelve records. Expected positions are +2.5/-2.5 in ShortLane and +5/-5 in LongLane, ten decisions each. `python examples/actor-control/generate_models.py` regenerates those ONNX files.

## Inspect results

Output is under `runs/<run-id>`. The root manifest indexes node statuses; each node manifest records configuration, dependencies, output artifacts and execution counts.

Training output contains `checkpoint.zip`, `model.onnx`, `training.yaml`, intermediate training checkpoints, `artifacts.json`, and Unity/container logs. Each trial retains its assignment and `output/episodes.jsonl`. Conversion files/logs live under `models/conversion-*`. Shared ONNX paths convert once per run. The runner removes its containers and Compose network after completion.

See [the DAG guide](../../docs/training-dag.md) for continuation rules, failure propagation and image contracts. Grading and aggregated comparison reports remain planned.