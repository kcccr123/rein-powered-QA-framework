# Adapter-driven actor sample

The same Linux Player runs ShortLane (0.25 units per decision) and LongLane
(0.5 units per decision). Embedded-weight ONNX policies move the actor right or
left. Swapping `agents[].model` requires no image rebuild. Each episode ends
after ten decisions; evidence records scene, position, decision count, and action.

`SampleActor` owns movement and the episode end condition. `SampleAdapter` owns
its actor reference and observation/action/evidence mapping. The inherited
`QaPlatformAdapter` owns model loading, CPU inference, decision timing, episode
counting, and reporting. The bootstrap only selects the assigned scene.

From the framework root on Windows:

```powershell
python -m pip install -e '.[platform,test]'
& 'C:\Program Files\Unity Hub\Unity Hub.exe' -- --headless install-modules --version 6000.3.4f1 --module linux-mono --errors
& 'C:\Program Files\Unity\Hub\Editor\6000.3.4f1\Editor\Unity.exe' -batchmode -nographics -quit -projectPath "$PWD/examples/actor-control" -executeMethod BuildSample.Build -logFile "$PWD/build-sample.log"
docker build -f examples/actor-control/Dockerfile -t qa-actor-sample:local .
python -m qa_framework.platform.cli examples/actor-control/suite.yaml
```

Wait for the Editor process to exit successfully and check `build-sample.log`
before invoking Docker. Linux Mono support is added to the existing Windows
Editor; Windows build support stays installed. The sample pins Unity 6000.3.4f1
and Inference Engine 2.4.1. `BuildSample.Build` regenerates its three scenes,
puts the bootstrap first, and builds `Build/Linux/ActorSample.x86_64`. Set
`QA_SAMPLE_BUILD_PATH` to override the build output during temporary checks.

The sample strips compute shader variants and marks App UI as editor-only because
its Player uses CPU inference. The image sets `SDL_VIDEODRIVER=dummy`; Unity
6000.3.4f1 otherwise crashes in native input startup without a display driver,
even with `-batchmode -nographics`.

Edit `model_preparation.unity_editor` in the suite for your machine. The supplied
suite runs two environments × two models × three episodes, at most two trials
at once. Expect twelve `episodes.jsonl` records: ShortLane positions +2.5/-2.5,
LongLane +5/-5, ten decisions each, action +1/-1. Each trial logs confirmed
storage and exits successfully; the runner removes its containers and network.
Both models use a static float input `[1, 2]` and float output `[1, 1]`.
`python examples/actor-control/generate_models.py` reproduces the included files.

Output under `runs/<suite-id>` includes preserved conversion artifacts/logs,
assignments, container logs, and each trial's Unity log and episode records.
Models are mounted read-only at `/qa/model/agent.sentis`. The manifest and
conversion log should show two imports, shared by four trials. No models are
baked into the image. This sample recipe is for verification; reusable production
base images, dungeon migration, GPU inference, and multi-input/recurrent models
remain separate work.
