# Isaac Franka Agent

An LLM plans, Isaac Sim executes.

## Rules
- Prioritize a working end-to-end demo over polish. Ask before large refactors.
- Do not write robot control from scratch. Low-level motion comes from Isaac Sim's stock Franka `PickPlaceController`.
- Verify Isaac Sim API usage against the installed version's `standalone_examples/.../franka/pick_place.py`. Module paths change between 5.x and 6.x.
- The `Backend` interface is the seam. Agent, prompts and evals must keep working against `MockBackend` with no GPU.
- Evals score the final simulator state, never the model's reply text.
- Keep README Scope/Results current as items land.
- Stop cloud GPU instances when idle.

## Commands
- `pytest -q`: offline tests (scripted model, no API key)
- `python -m franka_agent "..."`: mock backend with a live model
- `python -m franka_agent.evals`: eval suite (spends API credits)
- `./python.sh isaac/run_isaac.py`: Isaac Sim (GPU host)
