"""Isaac Sim 6.2 entrypoint: an LLM plans, Isaac Sim's cuMotion pick/place controller executes.

Run with Isaac Sim's bundled Python:

    ./python.sh isaac/run_isaac.py --smoke                      # no LLM: scripted moves, prints PASS/FAIL
    ./python.sh isaac/run_isaac.py --livestream                 # then type commands in the terminal
    ./python.sh isaac/run_isaac.py --livestream --commands "put the red cube on the blue one"

The scene setup, controller construction and per-move loop follow Isaac Sim 6.2.0's
source/standalone_examples/api/isaacsim.robot_motion.examples/manipulation/stacking.py
(Apache-2.0). That example runs one fresh PickPlaceController per pick/place goal; so do we,
one per LLM tool call.
"""

from __future__ import annotations

import argparse
import math
import queue
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--headless", action="store_true")
parser.add_argument("--livestream", action="store_true", help="headless + WebRTC livestream (implies --headless)")
parser.add_argument("--smoke", action="store_true", help="run scripted moves without an LLM and exit")
parser.add_argument("--commands", nargs="*", default=[], help="run these, then keep reading stdin")
parser.add_argument("--provider", default="ollama")
parser.add_argument("--model", default=None)
parser.add_argument("--effort", default="low")
args, _ = parser.parse_known_args()

from isaacsim import SimulationApp  # noqa: E402

if args.livestream:
    # Same launch config shape as standalone_examples/api/isaacsim.simulation_app/livestream.py.
    simulation_app = SimulationApp(launch_config={
        "width": 1280, "height": 720, "window_width": 1920, "window_height": 1080,
        "headless": True, "hide_ui": False,
    })
else:
    simulation_app = SimulationApp({"headless": args.headless})

import omni.kit.app  # noqa: E402

omni.kit.app.get_app().get_extension_manager().set_extension_enabled_immediate("isaacsim.robot_motion.examples", True)

import isaacsim.core.experimental.utils.app as app_utils  # noqa: E402
import isaacsim.robot_motion.experimental.motion_generation as mg  # noqa: E402
import numpy as np  # noqa: E402
import warp as wp  # noqa: E402
from isaacsim.core.experimental.objects import Cube  # noqa: E402
from isaacsim.core.experimental.prims import GeomPrim, RigidPrim  # noqa: E402
from isaacsim.core.simulation_manager import SimulationManager  # noqa: E402
from isaacsim.robot_motion.cumotion import RmpFlowController, load_cumotion_supported_robot  # noqa: E402
from isaacsim.robot_motion.examples.manipulation import (  # noqa: E402
    GripperCommand,
    JointGripperController,
    ManipulationScenario,
    PickPlaceController,
    PickPlacePhase,
)

if args.livestream:
    from isaacsim.core.experimental.utils.app import enable_extension

    simulation_app.set_setting("/app/window/drawMouse", True)
    enable_extension("omni.kit.livestream.app")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from franka_agent.agent import LanguageAgent  # noqa: E402
from franka_agent.backend import ActionResult, run_move  # noqa: E402
from franka_agent.world import CUBE_SIZE, DEFAULT_BLOCKS, TABLE_ZONES, Block, Scene  # noqa: E402

PHYSICS_DT = 1.0 / 60.0
ROBOT_PATH = "/World/robot"
SETTLE_SECONDS = 0.5  # cubes must be still this long before we read the result (as in stacking.py)
MAX_SETTLE_SECONDS = 4.0


def make_controller(scenario: ManipulationScenario) -> PickPlaceController:
    """RMPflow arm + Franka finger gripper, as in stacking.py's _make_controller (Franka branch)."""
    robot = load_cumotion_supported_robot(scenario.robot_config.name)
    tool_frame = scenario.robot_config.tool.controller_frame
    if tool_frame not in robot.robot_description.tool_frame_names():
        raise RuntimeError(f"cuMotion configuration does not support tool frame {tool_frame!r}.")
    arm = RmpFlowController(
        cumotion_robot=robot,
        cumotion_world_interface=scenario.world_interface,
        robot_joint_space=scenario.joint_space,
        robot_site_space=scenario.site_space,
        tool_frame=tool_frame,
    )
    gripper = scenario.robot_config.gripper
    common = {
        "robot_joint_space": scenario.joint_space,
        "joint_names": gripper.joint_names,
        "open_positions": gripper.open_positions,
        "closed_positions": gripper.closed_positions,
        "position_tolerance": gripper.position_tolerance,
        "velocity_tolerance": gripper.velocity_tolerance,
        "minimum_close_fraction": gripper.minimum_close_fraction,
    }
    return PickPlaceController(
        arm_controller=arm,
        gripper_open_controller=JointGripperController(**common, command=GripperCommand.OPEN),
        gripper_close_controller=JointGripperController(**common, command=GripperCommand.CLOSE),
        robot_site_space=scenario.site_space,
        tool_frame=tool_frame,
        controller_to_grasp_position=scenario.robot_config.tool.controller_to_grasp_position,
        controller_to_grasp_orientation=scenario.robot_config.tool.controller_to_grasp_orientation,
        grasp_orientation=scenario.robot_config.grasp_orientation,
        approach_height=0.20,
    )


def make_goal(pick, place) -> mg.RobotState:
    names = [PickPlaceController.PICK_SITE, PickPlaceController.PLACE_SITE]
    return mg.RobotState(
        sites=mg.SpatialState.from_name(
            spatial_space=names,
            positions=(names, wp.array([tuple(pick), tuple(place)], dtype=wp.float32, device="cpu")),
        )
    )


class IsaacBackend:
    def __init__(self, scenario: ManipulationScenario, cubes: dict[str, RigidPrim], cube_paths: dict[str, str]):
        self.scenario = scenario
        self.cubes = cubes
        self.cube_paths = cube_paths
        self.executor = ThreadPoolExecutor(max_workers=1)

    def _position(self, name: str) -> np.ndarray:
        return self.cubes[name].get_world_poses()[0].numpy()[0]

    def scene(self) -> Scene:
        return Scene.from_poses({
            name: Block(name, self._position(name), DEFAULT_BLOCKS[name][1]) for name in self.cubes
        })

    @staticmethod
    def _simulating() -> bool:
        return app_utils.is_playing() and SimulationManager.is_simulating()

    def _settle(self) -> None:
        still, elapsed = 0, 0
        need, cap = math.ceil(SETTLE_SECONDS / PHYSICS_DT), math.ceil(MAX_SETTLE_SECONDS / PHYSICS_DT)
        while simulation_app.is_running() and still < need and elapsed < cap:
            simulation_app.update()
            if self._simulating():
                elapsed += 1
                moving = False
                for cube in self.cubes.values():
                    lin, ang = cube.get_velocities()
                    moving |= np.linalg.norm(lin.numpy()[0]) > 0.05 or np.linalg.norm(ang.numpy()[0]) > 0.5
                still = 0 if moving else still + 1

    def _execute(self, obj: str, target: str) -> str | None:
        """One pick/place goal, mirroring stacking.py's per-cube loop. Returns a failure note or None."""
        path = self.cube_paths[obj]
        pick = self._position(obj)
        place = self.scene().target_position(target)
        controller = make_controller(self.scenario)
        goal = make_goal(pick, place)
        t = 0.0
        if not controller.reset(self.scenario.read_robot_state(), goal, t=t):
            return controller.failure_reason or "controller reset failed"
        obstacle_disabled = False
        note = None
        try:
            while simulation_app.is_running() and not controller.is_done and not controller.failed:
                simulation_app.update()
                if not self._simulating():
                    continue
                t += PHYSICS_DT
                self.scenario.sync_world()
                previous = controller.phase
                desired = controller.forward(self.scenario.read_robot_state(), goal, t)
                if desired is None:
                    note = controller.failure_reason or "controller returned no command"
                    break
                self.scenario.apply_robot_state(desired)
                if previous is not controller.phase:
                    # The cube being grasped must not be an obstacle while the fingers close on it.
                    if controller.phase is PickPlacePhase.DESCEND_PICK:
                        self.scenario.set_planning_obstacles_enabled((path,), False)
                        obstacle_disabled = True
                    elif controller.phase is PickPlacePhase.RETREAT:
                        self.scenario.set_planning_obstacles_enabled((path,), True)
                        obstacle_disabled = False
            if controller.failed:
                note = controller.failure_reason or "pick/place failed"
        finally:
            if obstacle_disabled:
                self.scenario.set_planning_obstacles_enabled((path,), True)
        self._settle()
        return note

    def pick_and_place(self, obj: str, target: str) -> ActionResult:
        return run_move(self.scene, self._execute, obj, target)

    def wait_for(self, fn):
        """Keep the app updating while the planner thinks, so the viewport never freezes."""
        future = self.executor.submit(fn)
        while not future.done():
            simulation_app.update()
        return future.result()


def build_scene() -> IsaacBackend:
    SimulationManager.setup_simulation(dt=PHYSICS_DT, device="cpu")
    scenario = ManipulationScenario("franka", robot_prim_path=ROBOT_PATH)
    scenario.setup_scene()

    cubes, paths = {}, {}
    for name, (pos, color) in DEFAULT_BLOCKS.items():
        path = f"/World/{name}"
        cube = Cube(path, positions=[pos.tolist()], sizes=CUBE_SIZE, colors=[list(color)])
        GeomPrim(cube.paths, apply_collision_apis=True)
        cubes[name], paths[name] = RigidPrim(cube.paths), path

    # Zone markers: visual-only cubes sunk into the ground so just a flat square shows.
    markers = []
    for zone, center in TABLE_ZONES.items():
        path = f"/World/marker_{zone}"
        Cube(path, positions=[[center[0], center[1], -CUBE_SIZE / 2 + 0.0005]], sizes=CUBE_SIZE * 1.4,
             colors=[[0.55, 0.55, 0.55]])
        markers.append(path)

    scenario.initialize_world_binding(exclude_prim_paths=tuple(markers))
    simulation_app.update()
    app_utils.play()
    simulation_app.update()
    scenario.articulation.reset_to_default_state()
    simulation_app.update()
    return IsaacBackend(scenario, cubes, paths)


SMOKE_MOVES = [("red_cube", "blue_cube"), ("red_cube", "table_left"), ("green_cube", "table_center")]


def smoke(backend: IsaacBackend) -> int:
    """Exercise the same skill the LLM uses, with no LLM in the loop."""
    backend._settle()
    failures = 0
    for obj, target in SMOKE_MOVES:
        result = backend.pick_and_place(obj, target)
        failures += not result.success
        print(f"[{'PASS' if result.success else 'FAIL'}] {obj} -> {target}: {result.message}", flush=True)
    print("SMOKE PASS" if failures == 0 else f"SMOKE FAIL ({failures}/{len(SMOKE_MOVES)} moves)", flush=True)
    return failures


def read_stdin(commands: queue.Queue) -> None:
    for line in sys.stdin:
        if line.strip():
            commands.put(line.strip())


def main() -> int:
    backend = build_scene()
    if args.smoke:
        return 1 if smoke(backend) else 0

    agent = LanguageAgent(backend, provider=args.provider, model=args.model, effort=args.effort)
    commands: queue.Queue[str] = queue.Queue()
    for c in args.commands:
        commands.put(c)
    threading.Thread(target=read_stdin, args=(commands,), daemon=True).start()
    print("Type a command and press Enter (e.g. 'stack red on blue').", flush=True)

    while simulation_app.is_running():
        simulation_app.update()
        try:
            agent.run(commands.get_nowait())
        except queue.Empty:
            pass
    return 0


if __name__ == "__main__":
    exit_code = 0
    try:
        exit_code = main()
    except BaseException:
        import traceback

        traceback.print_exc()
        exit_code = 1
    finally:
        simulation_app.close(exit_code=exit_code)
    if exit_code:
        raise SystemExit(exit_code)
