# Running on a cloud GPU

Isaac Sim needs an NVIDIA RTX GPU, so Apple Silicon Macs can't run it. The full click-by-click guide is in [AWS_WALKTHROUGH.md](AWS_WALKTHROUGH.md). It recommends a g6e.2xlarge, the `nvcr.io/nvidia/isaac-sim:6.2.0` container, and Ollama on the host. NVIDIA Brev is the fallback if the AWS quota stalls.

## First-run checklist

- [ ] Stock example passes: `./python.sh standalone_examples/api/isaacsim.robot_motion.examples/manipulation/stacking.py --headless --test`
- [ ] `./python.sh -m pip install openai`
- [ ] `run_isaac.py --smoke --headless` prints `SMOKE PASS` (the skill layer works with no LLM)
- [ ] `ollama run qwen3:8b "hi"` works on the host
- [ ] `run_isaac.py --livestream` streams to the WebRTC client and a command stacks correctly
- [ ] Record: 3 commands (simple stack, 3-block tower, a refusal) with the terminal beside the viewport
- [ ] **Stop the instance**
