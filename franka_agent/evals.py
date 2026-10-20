"""Command-suite eval: does the final scene satisfy what the user asked for?

Each case is scored on the simulator state, not on the model's reply, and is run
both with perfect grasps and with injected grasp failures to measure recovery.

    python -m franka_agent.evals --drop-rates 0 0.25 --seeds 3
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from typing import Callable

from .agent import PROVIDERS, LanguageAgent
from .backend import MockBackend
from .world import Scene


@dataclass
class Case:
    name: str
    command: str
    goal: Callable[[Scene], bool]
    min_moves: int
    setup: tuple[tuple[str, str], ...] = ()

    @property
    def is_refusal(self) -> bool:
        return self.min_moves == 0


def _unchanged(scene: Scene) -> bool:
    return all(s == "table" for s in scene.support.values())


CASES = [
    Case("simple_stack", "Put the red cube on the blue one.",
         lambda s: s.support["red_cube"] == "blue_cube", 1),
    Case("zone_move", "Move the green cube over to the left side of the table.",
         lambda s: s.support["green_cube"] == "table" and s.zone_occupant("table_left") == "green_cube", 1),
    Case("three_tower", "Build a tower: blue on the bottom, then green, then red on top.",
         lambda s: s.support["green_cube"] == "blue_cube" and s.support["red_cube"] == "green_cube", 2),
    Case("implicit_middle", "Stack all three cubes with green at the bottom and red on top.",
         lambda s: s.support["blue_cube"] == "green_cube" and s.support["red_cube"] == "blue_cube", 2),
    Case("needs_clearing", "Put the blue cube on the red cube.",
         lambda s: s.support["blue_cube"] == "red_cube", 2, setup=(("red_cube", "blue_cube"),)),
    Case("unstack", "Unstack everything so every cube sits on the table.",
         _unchanged, 2, setup=(("green_cube", "blue_cube"), ("red_cube", "green_cube"))),
    Case("unknown_object", "Put the purple cube on the red one.", _unchanged, 0),
    Case("self_reference", "Place the red cube on top of the red cube.", _unchanged, 0),
]


def run_case(case: Case, drop_rate: float, seed: int, **agent_kwargs) -> dict:
    backend = MockBackend(seed=seed)
    for obj, target in case.setup:
        assert backend.pick_and_place(obj, target).success
    backend.drop_rate, backend.moves = drop_rate, 0

    agent = LanguageAgent(backend, verbose=False, **agent_kwargs)
    t0 = time.time()
    try:
        result = agent.run(case.command)
        reply, error = result.reply, None
    except Exception as exc:
        reply, error = "", repr(exc)
    passed = error is None and case.goal(backend.scene())
    if case.is_refusal:
        passed = passed and backend.moves == 0
    return {
        "case": case.name, "drop_rate": drop_rate, "seed": seed, "passed": passed,
        "moves": backend.moves, "min_moves": case.min_moves,
        "seconds": round(time.time() - t0, 1), "reply": reply, "error": error,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--drop-rates", type=float, nargs="+", default=[0.0, 0.25])
    parser.add_argument("--seeds", type=int, default=2)
    parser.add_argument("--provider", choices=sorted(PROVIDERS), default="ollama")
    parser.add_argument("--model", default=None)
    parser.add_argument("--effort", default="low", help="reasoning effort (Ollama thinking models and Claude)")
    parser.add_argument("--out", default="eval_results.json")
    args = parser.parse_args()

    rows = []
    for drop in args.drop_rates:
        for case in CASES:
            for seed in range(args.seeds):
                row = run_case(case, drop, seed, provider=args.provider, model=args.model, effort=args.effort)
                rows.append(row)
                print(f"[{'PASS' if row['passed'] else 'FAIL'}] drop={drop:.2f} {case.name:<16} "
                      f"moves={row['moves']} (min {case.min_moves}) {row['seconds']}s")

    print("\nsuccess rate by grasp-failure rate:")
    for drop in args.drop_rates:
        sub = [r for r in rows if r["drop_rate"] == drop]
        print(f"  drop={drop:.2f}: {sum(r['passed'] for r in sub)}/{len(sub)}")
    with open(args.out, "w") as f:
        json.dump(rows, f, indent=2)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
