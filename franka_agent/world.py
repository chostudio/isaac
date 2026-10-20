"""Scene model shared by every backend.

The scene is derived from block poses alone, so the mock and Isaac Sim backends
verify actions with exactly the same code: read poses, rebuild relations, compare.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# Cube edge length used by Isaac Sim 6.2's stock pick_place.py / stacking.py examples (meters).
CUBE_SIZE = 0.05

# Named spots on the table the arm can place a block on (x forward, y left, robot base at
# the origin). Kept within the region Isaac Sim 6.2's stacking.py exercises: x around 0.45,
# y from -0.2 to 0.35.
TABLE_ZONES: dict[str, np.ndarray] = {
    "table_left": np.array([0.58, 0.2, 0.0]),
    "table_center": np.array([0.58, 0.0, 0.0]),
    "table_right": np.array([0.58, -0.2, 0.0]),
}

# Where each block starts.
DEFAULT_BLOCKS: dict[str, tuple[np.ndarray, tuple[float, float, float]]] = {
    "red_cube": (np.array([0.40, 0.2, CUBE_SIZE / 2]), (1.0, 0.1, 0.1)),
    "green_cube": (np.array([0.40, 0.0, CUBE_SIZE / 2]), (0.1, 0.8, 0.1)),
    "blue_cube": (np.array([0.40, -0.2, CUBE_SIZE / 2]), (0.1, 0.2, 1.0)),
}

XY_TOL = 0.02  # how far off-center a stacked block may sit (m)
Z_TOL = 0.012  # stacking height tolerance (m)


@dataclass
class Block:
    name: str
    position: np.ndarray
    color: tuple[float, float, float]


@dataclass
class Scene:
    blocks: dict[str, Block]
    # block -> what it rests on ("table" or another block name)
    support: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_poses(cls, blocks: dict[str, Block]) -> "Scene":
        scene = cls(blocks=blocks)
        for name, b in blocks.items():
            scene.support[name] = "table"
            for other, o in blocks.items():
                if other == name:
                    continue
                dz = b.position[2] - o.position[2]
                dxy = np.linalg.norm(b.position[:2] - o.position[:2])
                if abs(dz - CUBE_SIZE) < Z_TOL and dxy < CUBE_SIZE * 0.6:
                    scene.support[name] = other
        return scene

    def is_clear(self, name: str) -> bool:
        """True when nothing is resting on top of this block."""
        return name not in self.support.values()

    def on_top_of(self, name: str) -> str | None:
        return next((b for b, s in self.support.items() if s == name), None)

    def zone_occupant(self, zone: str) -> str | None:
        center = TABLE_ZONES[zone]
        for name, b in self.blocks.items():
            if self.support[name] == "table" and np.linalg.norm(b.position[:2] - center[:2]) < CUBE_SIZE:
                return name
        return None

    def target_position(self, target: str) -> np.ndarray:
        """Where a block's center should end up when placed on `target`."""
        if target in TABLE_ZONES:
            return TABLE_ZONES[target] + np.array([0.0, 0.0, CUBE_SIZE / 2])
        return self.blocks[target].position + np.array([0.0, 0.0, CUBE_SIZE])

    def describe(self) -> dict:
        """JSON-friendly snapshot handed to the LLM."""
        return {
            "blocks": {
                name: {
                    "position_m": [round(float(v), 3) for v in b.position],
                    "resting_on": self.support[name],
                    "has_block_on_top": self.on_top_of(name),
                }
                for name, b in self.blocks.items()
            },
            "table_zones": {
                zone: {"occupied_by": self.zone_occupant(zone)} for zone in TABLE_ZONES
            },
        }


def check_preconditions(scene: Scene, obj: str, target: str) -> str | None:
    """Return a reason the move is impossible, or None if it can be attempted."""
    if obj not in scene.blocks:
        return f"unknown object '{obj}'"
    if target not in scene.blocks and target not in TABLE_ZONES:
        return f"unknown target '{target}'"
    if obj == target:
        return "cannot place a block on itself"
    if not scene.is_clear(obj):
        return f"{obj} has {scene.on_top_of(obj)} on top of it; move that first"
    if target in scene.blocks and not scene.is_clear(target):
        return f"{target} already has {scene.on_top_of(target)} on top of it"
    if target in TABLE_ZONES and scene.zone_occupant(target):
        return f"{target} is occupied by {scene.zone_occupant(target)}"
    return None


def verify_placement(scene: Scene, obj: str, target: str) -> bool:
    """Did `obj` actually end up on `target`, judged from poses alone?"""
    if target in TABLE_ZONES:
        expected = TABLE_ZONES[target][:2]
        pos = scene.blocks[obj].position
        return scene.support[obj] == "table" and np.linalg.norm(pos[:2] - expected) < XY_TOL * 2
    return scene.support[obj] == target
