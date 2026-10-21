# Isaac Franka Agent

An LLM plans, Isaac Sim executes.

## Rules
- Prioritize a working end-to-end demo over polish. Ask before large refactors.
- Do not write robot control from scratch. Low-level motion comes from Isaac Sim's stock Franka `PickPlaceController`.
- Verify Isaac Sim API usage against the installed version's `standalone_examples/api/isaacsim.robot_motion.examples/manipulation/stacking.py`. The 5.x API is incompatible with 6.x.
- The `Backend` interface is the seam. Agent, prompts and evals must keep working against `MockBackend` with no GPU.
- Evals score the final simulator state, never the model's reply text.
- Keep README Scope/Results current as items land.
- Stop cloud GPU instances when idle.

## Commands
- `pytest -q`: offline tests (scripted model, no API key)
- `python -m franka_agent "..."`: mock backend with a live model (Nemotron by default; `--provider ollama` is free and local; `--lang ja` adds Riva Translate)
- `python -m franka_agent.evals`: eval suite (calls the live model for every case)
- `./python.sh isaac/run_isaac.py`: Isaac Sim (GPU host)
