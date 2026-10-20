"""Backends execute one pick-and-place and report what physically happened.

`Backend` is the seam between the language layer and the simulator. The mock
backend runs anywhere (no GPU) so the agent and evals can be developed on a
laptop; `isaac/run_isaac.py` provides the Isaac Sim implementation.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable, Protocol, TypeVar

import numpy as np

from .world import CUBE_SIZE, DEFAULT_BLOCKS, Block, Scene, check_preconditions, verify_placement

T = TypeVar("T")


@dataclass
class ActionResult:
    success: bool
    message: str
    scene: dict

    def to_tool_result(self) -> dict:
        return {"success": self.success, "message": self.message, "scene": self.scene}


class Backend(Protocol):
    def scene(self) -> Scene: ...

    def pick_and_place(self, obj: str, target: str) -> ActionResult: ...

    def wait_for(self, fn: Callable[[], T]) -> T:
        """Run a blocking call (an LLM request) while keeping the sim alive."""
        ...


def run_move(backend_scene: Callable[[], Scene], execute: Callable[[str, str], str | None],
             obj: str, target: str) -> ActionResult:
    """Shared move flow: precondition check, execute, re-read poses, verify.

    `execute` may return a note (e.g. a controller failure reason) that is passed on to the
    planner when verification fails.
    """
    before = backend_scene()
    reason = check_preconditions(before, obj, target)
    if reason:
        return ActionResult(False, f"refused: {reason}", before.describe())
    note = execute(obj, target)
    after = backend_scene()
    if verify_placement(after, obj, target):
        return ActionResult(True, f"{obj} is now on {target}", after.describe())
    detail = f" (controller: {note})" if note else ""
    return ActionResult(
        False,
        f"executed, but verification failed: {obj} is resting on {after.support[obj]}, not {target}{detail}",
        after.describe(),
    )


class MockBackend:
    """Kinematic stand-in for Isaac Sim with optional grasp failures.

    `drop_rate` makes a fraction of grasps slip and leave the block at a random
    free spot on the table, which exercises the agent's verify-and-retry loop.
    """

    def __init__(self, drop_rate: float = 0.0, seed: int | None = None):
        self.blocks = {
            name: Block(name, pos.copy(), color) for name, (pos, color) in DEFAULT_BLOCKS.items()
        }
        self.drop_rate = drop_rate
        self.rng = random.Random(seed)
        self.moves = 0

    def scene(self) -> Scene:
        return Scene.from_poses(self.blocks)

    def _execute(self, obj: str, target: str) -> None:
        self.moves += 1
        if self.rng.random() < self.drop_rate:
            # Grasp slipped: the block lands on a free patch of table off its path.
            for _ in range(50):
                spot = np.array([0.49 + self.rng.uniform(-0.03, 0.03), self.rng.uniform(-0.3, 0.3), CUBE_SIZE / 2])
                if all(np.linalg.norm(spot[:2] - b.position[:2]) > CUBE_SIZE * 1.5
                       for n, b in self.blocks.items() if n != obj):
                    break
            self.blocks[obj].position = spot
            return
        self.blocks[obj].position = self.scene().target_position(target)

    def pick_and_place(self, obj: str, target: str) -> ActionResult:
        return run_move(self.scene, self._execute, obj, target)

    def wait_for(self, fn: Callable[[], T]) -> T:
        return fn()
