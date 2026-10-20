# Isaac Franka Agent

**Tell a robot arm what you want in plain English. An LLM (a free local model by default, or Claude/Nemotron) plans the moves, NVIDIA Isaac Sim runs the physics, and every step is checked against the simulator before the next one.**

> "Build a tower: blue on the bottom, then green, then red on top."
>
> `get_scene` → `pick_and_place(green_cube → blue_cube)` ✓ → `pick_and_place(red_cube → green_cube)` ✓
> "Done: blue on the table, green on blue, red on top."

A Franka Panda arm works on three colored cubes. A language model handles the high-level reasoning: breaking a request into moves, ordering them, clearing a covered block first, and refusing requests that don't make sense. Isaac Sim's built-in `PickPlaceController` handles the low-level motion. The two meet at a narrow, verified skill interface.

---

## Why it's built this way

Language models are good at *what* and *in what order*. They are bad at joint angles. So the model gets **skills, not coordinates**:

| Tool | What it does |
|---|---|
| `get_scene` | Block positions, what each block rests on, which table zones are free |
| `pick_and_place(object, target)` | Move one clear block onto another block or an empty zone |

Three design decisions do most of the work:

1. **Closed loop, measured from physics.** After every move the backend re-reads the cube poses from the simulator and rebuilds the scene (which block is on which). The model is told what *happened*, not what it asked for. A grasp that slips comes back as `success: false` along with the real scene, and the model re-plans.
2. **One action per turn.** Parallel tool calls are disabled, so the planner never chains a second move onto a first one it hasn't seen land.
3. **Constrained outputs.** Tool arguments use strict JSON schemas with `enum`s of real object names, so the model can't make up a `purple_cube`. Physical preconditions (a covered block can't be picked) are enforced in code, and the reason is returned to the model.

```
 user text ─▶ LLM (planner) ────tool call──▶ skill layer ──▶ Backend
                   ▲                         preconditions     ├─ MockBackend   (laptop, no GPU)
                   │                         + verification    └─ IsaacBackend  (Isaac Sim, Franka
                   └──────── verified scene ◀──────────────────                  PickPlaceController)
```

The `Backend` interface is the seam. The same agent, prompts and evals run against a pure-Python mock on a laptop and against Isaac Sim on a GPU box.

---

## Scope

### Now: the MVP (this repo, built in one sitting)

- [x] Scene model with stacking relations derived from poses alone (`franka_agent/world.py`)
- [x] Skill layer with precondition checks and physics-based verification (`franka_agent/backend.py`)
- [x] LLM tool-use planner: one action per turn, re-plans on failure. Pluggable provider: **Ollama (free, local, default)**, NVIDIA API Catalog (Nemotron), or Claude (`franka_agent/agent.py`)
- [x] Mock backend with **injected grasp failures** to exercise recovery
- [x] **Eval suite**: 8 commands (simple stack, zone move, 3-block towers, implicit ordering, clearing a covered block, unstacking, unknown objects, self-reference), each scored on the *final simulator state*, at 0% and 25% grasp-failure rates (`franka_agent/evals.py`)
- [x] Isaac Sim **6.2** entrypoint built from the 6.2.0 `stacking.py` example: Franka with cuMotion RMPflow `PickPlaceController`, 3 dynamic cubes, zone markers, WebRTC livestream, and an LLM-free `--smoke` test. Rendering continues while the planner thinks (`isaac/run_isaac.py`)
- [x] Offline tests with a scripted stand-in for the model (`tests/`)
- [ ] Run on an Isaac Sim GPU instance (`--smoke` first), record the 60-second demo (see `cloud/AWS_WALKTHROUGH.md`)
- [ ] Publish eval numbers from a live run

### Next (weeks 1–2)

- **Language → perception.** Remove the ground-truth poses: render an RGB-D camera in Isaac Sim and let a vision model (Claude vision, or a Grounding DINO/SAM detector) locate the blocks. The skill layer stays the same and only the source of `scene()` changes.
- **Synthetic data with Replicator.** Domain-randomize cube color, lighting, texture and table to build a labeled dataset, then fine-tune a small detector and compare it with the zero-shot VLM in the eval.
- **Richer skills.** `push`, `rotate`, `place_at(x, y)`, and spatial relations ("left of", "between") with a geometric checker.
- **Eval dashboard.** Success rate, moves vs. optimal, recovery rate, and latency per command class, tracked across model versions and effort levels.

### Grand scope (where this goes)

- **Learned low-level skills in Isaac Lab.** Replace the scripted controller with RL or imitation-learned pick/place policies (PyTorch, thousands of parallel envs), and keep the LLM as the high-level planner. The planner/skill split stays the same, only the skills get better.
- **Robot foundation models.** Put a VLA such as NVIDIA **Isaac GR00T N** under the same planner. The LLM planner decomposes long-horizon tasks into short language subgoals, and GR00T executes each one from pixels.
- **Cosmos for world variation.** Use Cosmos world foundation models to generate photoreal variations of the scene for robustness testing and sim-to-real data.
- **Sim-to-real.** Run the same agent over ROS 2 (Isaac ROS) on a real arm, with Isaac Sim as the digital twin for pre-checking each plan before the real robot moves.
- **Multi-robot and humanoid.** Several arms sharing a workspace, or a humanoid in a kitchen scene, with the planner handling task allocation.

---

## Run it

### Laptop (no GPU): mock backend, free local model

```bash
pip install -r requirements.txt
brew install ollama && ollama serve &           # free, runs on Apple Silicon
ollama pull qwen3:8b

python -m franka_agent "put the red cube on the blue one"
python -m franka_agent --drop-rate 0.3          # interactive, with slippery grasps
python -m franka_agent.evals --drop-rates 0 0.25 --seeds 2
pytest -q                                       # offline, no model needed
```

Other providers: `--provider nvidia` (set `NVIDIA_API_KEY` from build.nvidia.com) or `--provider anthropic` (`ANTHROPIC_API_KEY`). Use `--model` to override the default model.

### Isaac Sim 6.2 (NVIDIA RTX GPU)

```bash
./python.sh -m pip install openai
./python.sh isaac/run_isaac.py --smoke --headless          # scripted moves, no LLM: expect SMOKE PASS
./python.sh isaac/run_isaac.py --livestream \
    --commands "put the red cube on the blue one" "now build a tower with green on the bottom"
```

Type more commands into the terminal while it runs. Step-by-step AWS setup (instance, quota, ports, streaming client, Ollama on the box, cost guardrails): [`cloud/AWS_WALKTHROUGH.md`](cloud/AWS_WALKTHROUGH.md).

The Isaac code targets **Isaac Sim 6.2.0** (`nvcr.io/nvidia/isaac-sim:6.2.0`). It mirrors the 6.2.0 source of `standalone_examples/api/isaacsim.robot_motion.examples/manipulation/stacking.py`. Earlier 5.x releases use a different, incompatible API.

---

## Repo layout

```
franka_agent/
  world.py      scene model: poses -> stacking relations, preconditions, verification
  backend.py    Backend interface, shared move/verify flow, MockBackend
  agent.py      LLM tool-use planner (Ollama / NVIDIA / Claude)
  evals.py      command suite scored on final sim state
  __main__.py   CLI against the mock backend
isaac/
  run_isaac.py  Isaac Sim world + IsaacBackend (stock Franka PickPlaceController)
cloud/         AWS walkthrough + first-run checklist
tests/          offline tests with a scripted model
```

## Results

| Setting | Success |
|---|---|
| Mock, 0% grasp failures | _pending live run_ |
| Mock, 25% grasp failures | _pending live run_ |
| Isaac Sim | _pending GPU run_ |
