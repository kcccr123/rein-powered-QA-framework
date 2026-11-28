# Dungeon navigation evaluation

[Evaluation DAG](evaluation.yaml) · [Models and checkpoint provenance](models/README.md)

QA-50 and QA-86 each play the same two 100-episode trials with frozen policies. Navigator 4 maps 128 ray directions and the policy receives the original 64 directions. Each movement decision lasts 0.1 simulated seconds. An episode records whether the player touched the game exit before death or the action limit.

| Unity scene | Cases per model | Action limit |
| --- | --- | --- |
| MixedRooms | Seventeen each with 1, 2, 3 and 4 rooms, sixteen each with 5 and 6 rooms | 600 for one room, 1,800 for two through six |
| Cathedral | 100 procedural cathedrals, seeds 768000 through 768099 | 1,800 |

MixedRooms cycles through the six room counts. Its 17 single-room seeds start at 481002 and advance by three through the QA-84 cases. The other 83 seeds run from 766000 through 766082 in the QA-85 expansive layout builder. The single-room and expansive layouts retain their original geometry, spawn, exit and time budgets. This combined 100-case sample has its own denominator when comparing it with older results.

Cathedral uses the game's cathedral generator and its authored settings, including the column and arch prefabs. The cathedral commit is pinned in the build script. Both models receive the same seed for each corresponding episode.

Cathedral trials default to 1,800 actions. Set `environment.parameters.action_limit` to a positive action count to change the time budget. The double-time suite uses 3,600 actions (360 simulated seconds) with the same 100 seeds and the local 3,000-episode waypoint checkpoint. [Double-time DAG](cathedral-double-time.yaml)

The 500-episode ray comparison uses five parallel Cathedral trials with the same frozen checkpoint and 3,600-action limit. Each trial starts at seed 768000 plus its `seed_offset`. Offsets of 0, 100, 200, 300, and 400 cover seeds 768000–768499 once each. The first 100 match the historical 64-ray trial. Each report records the navigation and policy ray counts. [500-episode DAG](cathedral-128-rays.yaml)

## Build the environments

From this directory on Windows, run `./build.ps1` in PowerShell. It builds a Linux Mono Player with all three scenes, then packages it as `qa-dungeon-trials:local`. It uses Unity 6000.3.4f1 with Linux Build Support, Docker and the sibling Unity RL and game repositories. The script reads game revision `2d43368` and cathedral revision `4d97815` directly from Git. It unpacks the historical navigation and QA-85 layout sources, then copies the current sensor, Navigator 4, map search, and waypoint clearance sources from the game checkout for both builds. [Frozen navigation sources](fixtures/qa85-navigation-sources.zip)

## Review an evaluation

The [two-branch frontier replay](cathedral-frontier-branches.yaml) runs the same nine Cathedral seeds through each navigator variant and collects both branches. Cathedral accepts `environment.parameters.grid_cell_size` in world units (default 0.25) and `reject_by_approach` (default false). The directed branch remembers a failed movement from its starting cell to its target cell. The fine-grid branch uses 0.125-unit cells and the original whole-cell rejection. Each episode reports both effective settings alongside its movement trace.

The [stall-memory replay](cathedral-stall-memory.yaml) compares three branches on the 0.25-unit grid. `stall_memory_actions` sets the maximum age of a stalled direction in completed actions. Zero, the default, disables age-based expiry. A repeated stall at the same spot and heading restarts its timer. The four matched-blockage trials set `expire_after_exhaustion` to enable that lifetime on the decision after the first exhaustion. The trace records each expiry and the subsequent player movements.

For a Cathedral diagnostic replay, set `environment.parameters.trace_navigation` to `true`. The collected episode's `navigation_trace` names a gzip JSONL file in its originating trial's `output/` directory. Each line pairs a one-based action number with its pre-action rays, post-action movement, planner events, and route checks. Ray hits use 0 for a miss, 1 for a wall, and 2 for the exit. On entry into `observed_space_exhausted`, the line also names a map snapshot in the same directory. Its rows start at `min_z` and columns at `min_x`, both in grid cells. A space marks unknown space, `.` observed floor, `v` visited floor, `#` a wall, and `E` an observed exit. Each frontier includes its eligibility decision. [Frontier replay DAG](cathedral-frontier-replay.yaml)

After a run, open `runs/<run-id>/nodes/collect/dataset/index.json` for trial identities, expected and received counts, and collection errors. The adjacent `episodes.jsonl` contains one record per episode with the model and environment IDs, seed, room count, exit discovery and completion, end reason, steps and simulated time. The framework collects the raw records without a custom analysis script.

`report.evidence.movement` contains one ordered sample per action, including the final action. Start the path at `report.evidence.spawn` and append each sample's world-space `position`. The first sample gives the position after action 1. Actions are spaced 0.1 simulated seconds apart. Each sample also includes the movement command passed to the player controller as `move_x` and `move_z`, and `has_waypoint` records whether the navigator supplied a waypoint for that action. Earlier runs recorded only the episode summary.

The diagnostic trace records `waypoint`, `waypoint_version`, and `navigation_status` at decision time. Read the waypoint position when `has_waypoint` is true. Episode evidence also records `viewed_open_cells`, `recovery_attempts`, and `first_exit_discovery_step`. Discovery step 0 indicates discovery at spawn and −1 indicates the exit stayed undiscovered. [Cathedral-only comparison of the three QA-86 checkpoints](cathedral-comparison.yaml)

## Train QA-86 on Cathedral

Run `./build.ps1 -Training` from this directory to build the cathedral training Player and image. The training build derives its scene from the Cathedral trial to share the level generator, movement controller, observation contract, and Navigator 4. The training rewards are +50 for reaching the exit, up to +5 for waypoint progress, and −0.001 per action.

Run `qa-run-suite examples/dungeon-navigation/cathedral-training.yaml` from the framework root. The DAG resumes the QA-86 PPO checkpoint for exactly 1,000 episodes on seeds 769000–769999, with a maximum of 1,800 actions per episode. It exports the final checkpoint to ONNX and runs the frozen policy on the 100 original cathedral seeds (768000–768099). Open `nodes/train-cathedral/output/episodes.jsonl` for the training episode records and `nodes/collect/dataset/` for the framework's training manifest, optimizer metrics and frozen trial records. [Training DAG](cathedral-training.yaml)

## Train waypoint following

Run `qa-run-suite examples/dungeon-navigation/waypoint-following-training.yaml` from the framework root after `./build.ps1 -Training`. It resumes original QA-86 on 1,000 fresh Cathedral seeds (772000–772999). Each episode runs for 600 actions at 0.1 simulated seconds per action. The training adapter assigns reachable waypoints, periodically pauses for 0.5–1.5 simulated seconds, and sometimes replaces an active waypoint before arrival. Reaching each waypoint earns +1. Signed progress on the current waypoint earns 0.05 per world unit closer and subtracts the same amount per unit farther away. Enemies are disabled during this drill. The exit remains detectable by the normal sensors and requires interaction for completion.

The training output records arrivals, random handoffs, pauses, and actions during pauses per episode. The DAG exports the checkpoint and runs the frozen policy through the unchanged 100-layout Cathedral evaluation. [Waypoint-following DAG](waypoint-following-training.yaml)
