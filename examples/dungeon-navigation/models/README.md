# Dungeon navigation models

These policies control the QA player in Kawaii Power Little Adventure. QA-50 and QA-86 are the baseline checkpoints. The full-Cathedral PPO policy is the strongest tested Cathedral candidate, with 192 exits on 200 held-out seeds.

| Policy | Saved model steps | Resume training | Run frozen trials |
| --- | ---: | --- | --- |
| QA-50 | 656,793 | [PPO checkpoint](qa-50/checkpoint.zip) | [ONNX policy](qa-50/model.onnx) |
| QA-86 | 1,641,793 | [PPO checkpoint](qa-86/checkpoint.zip) | [ONNX policy](qa-86/model.onnx) |
| Cathedral PPO | 44,091 | [PPO checkpoint](cathedral-ppo/checkpoint.zip) | [Sampled-policy DAG](../cathedral-ppo-holdout.yaml) |

For a workflow saved in `examples/dungeon-navigation/`, set the train node's `checkpoint` to `models/qa-50/checkpoint.zip` or `models/qa-86/checkpoint.zip`. A suite node can select both policies with these entries.

```yaml
agents:
  - id: qa-50
    model: models/qa-50/model.onnx
  - id: qa-86
    model: models/qa-86/model.onnx
```

The dungeon environment supplies the `local-waypoint-v1` contract. The input is 134 floats covering the previous movement, wall and exit rays, and the navigator's waypoint. The two outputs are movement along world X and Z, applied every 0.1 simulated seconds. The game supplies the navigator and applies zero movement while it has no waypoint.

The framework checkpoints preserve the original policy, value function, optimizer tensors, and training counters. Their constant PPO schedules use portable numeric settings. The QA-86 checkpoint also uses the training image's NumPy serialization for its saved observation, episode-start flag, and Gym spaces. Original game paths, artifact hashes, and action comparisons are in the [model manifest](manifest.json).

The Cathedral holdout DAG uses the [unclipped actor mean](cathedral-ppo/mean.onnx) with the checkpoint's learned per-axis standard deviations. The trial draws actions using the layout seed and then applies the normal action clamp. The ordinary [deterministic export](cathedral-ppo/deterministic.onnx) reached 92 exits on the 100-seed validation set. [Held-out results](cathedral-ppo/holdout-results.json) · [Historical Cathedral experiment models](cathedral-experiment-models.json)
