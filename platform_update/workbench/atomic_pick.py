"""Independent pickup, carry and placement on the existing MuJoCo scene."""
from functools import partial
from pathlib import Path
import hashlib

import numpy as np

from contracts import evaluate
from .plugin_api import TaskPlugin


def interpolate(env, targets):
    from sim import FPS
    for target, duration, stage in targets:
        start = env.action.copy()
        frames = round(duration * FPS)
        for frame in range(1, frames + 1):
            u = frame / frames
            yield start + (target - start) * (u * u * (3 - 2 * u)), stage


def plan(env, steps):
    targets = []
    q = env.action[:6]
    for xyz, aperture, duration, stage in steps:
        q = env.ik(xyz, seed=q)
        targets.append((np.r_[q, aperture], duration, stage))
    return targets


def pickup_steps(env):
    from .core import waypoints
    steps = waypoints(env)[:4]
    return [(*step[:3], stage) for step, stage in zip(
        steps, ("approach", "approach", "grasp", "lift")
    )] + [(steps[-1][0], 0., .6, "lift")]


def held_entry(sample):
    return bool(sample["held_over_interval"] and sample["finger_contacts"].all()
                and not sample["table_contact"]
                and np.linalg.norm(sample["cube_velocity"][:3]) < .02
                and np.linalg.norm(sample["cube_velocity"][3:]) < .15)


def prepare_recording(task, env):
    obs = env.observe()
    if task == "pick":
        valid = obs["table_contact"] and not obs["finger_contacts"].any() and obs["state"][6] > .07
    else:
        valid = held_entry(obs)
        if task == "place":
            valid = valid and np.linalg.norm(obs["cube_xyz"][:2] - env.goal_xy) < .025
    if not valid:
        raise ValueError("当前物体状态不满足任务起始条件，请先重置场景")


def make_environment(task, recipe, render, recorded_xml):
    from .core import make_environment as make_base

    env = make_base(render, "pick_place", recorded_xml, recipe)
    reset_base = env.reset_scene
    env.task_name = task

    def reset_scene(cube, goal, seed=0):
        obs = reset_base(cube, goal, seed)
        if task != "pick" and not recorded_xml:
            # Roll the pickup through contact physics before recording begins.
            steps = pickup_steps(env)
            if task == "place":
                xyz = np.r_[goal, .23]
                steps += [(xyz, 0., 2., "transfer"), (xyz, 0., .6, "hold")]
            for action, _ in interpolate(env, plan(env, steps)):
                env.step(action)
            prepare_recording(task, env)
            env.data.time = 0
            env.initial_cube = env.cube_position().copy()
            env.start_xy = env.initial_cube[:2].copy()
            obs = env.observe()
        return obs

    env.reset_scene = reset_scene
    return env


def preflight(task, env):
    from .core import waypoints
    if task == "pick":
        steps = pickup_steps(env)
    elif task == "move_held_object":
        xyz = np.r_[env.goal_xy, .23]
        steps = [(xyz, 0., 2., "transfer"), (xyz, 0., .65, "hold")]
    elif task == "shake":
        xyz = np.r_[env.initial_cube[:2], .23]
        steps = [(xyz + [0, offset, 0], 0., .4, "shake")
                 for offset in (.045, -.045, .045, -.045, .045, -.045)]
        steps += [(xyz, 0., .4, "settle"), (xyz, 0., .65, "settle")]
    else:
        steps = [(*step[:3], stage) for step, stage in zip(
            waypoints(env)[5:], ("lower", "release", "retreat", "retreat")
        )]
    return plan(env, steps)


def check_condition(start, goal):
    # Position sampling applies the recipe distance before physical reset.
    # Placement starts over the goal and therefore replays with coincident XY.
    return None


def contract(task, samples, stages, start, goal, pick_profile=None):
    skill = {"pick": "grasp", "shake": "move_held_object"}.get(task, task)
    segment = evaluate(samples, [skill] * len(stages), goal, pick_profile)["segments"][0]
    checks = dict(segment["termination_checks"])
    if task == "pick":
        checks["initial_object_supported_and_gripper_open"] = segment["entry_state_evidence"]
        checks["lift_at_start"] = bool(np.linalg.norm(samples[-1]["cube_xyz"][:2] - start) < .035)
    else:
        checks["initial_object_held_and_still"] = held_entry(samples[0])
        if task == "place":
            checks["starts_above_goal"] = bool(
                np.linalg.norm(samples[0]["cube_xyz"][:2] - goal) < .025
                and samples[0]["cube_xyz"][2] > .12
            )
        elif task == "move_held_object":
            checks["object_translated"] = bool(np.linalg.norm(samples[-1]["cube_xyz"][:2] - start) >= .06)
        else:
            checks.pop("placement_ready")
            offsets = np.array([s["cube_xyz"][1] - start[1] for s in samples])
            signs = np.sign(offsets[np.abs(offsets) >= .032])
            checks["three_oscillations"] = bool(np.count_nonzero(np.diff(signs)) >= 5)
            checks["returns_to_origin"] = bool(np.linalg.norm(samples[-1]["cube_xyz"][:2] - start) < .025)
    return dict(contract_version=f"piper_{task}_v1", execution_unit=task,
                checks=checks, success=all(checks.values()),
                independent_learned_policy_validated=False)


def register(catalog, plugins):
    source = Path(__file__)
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    definitions = {
        "pick": ("抓取", "Pick up the red cube and hold it above the table.",
                 "从桌面抓起物体，在起点上方稳定夹持。", ("approach", "grasp", "lift")),
        "move_held_object": ("持物移动", "Carry the already held red cube above the green target area.",
                             "从已稳定夹持开始，持物到达绿区上方并保持。", ("start", "transfer", "hold")),
        "place": ("放置", "Lower the held red cube onto the green target area and release it.",
                  "从目标上方已夹持状态开始，下降、释放并退开。", ("lower", "release", "retreat")),
        "shake": ("夹持摇晃", "Shake the held red cube laterally three times, then hold it still.",
                  "已夹持物体沿世界Y轴往复三次；检查物体真实振幅、返回起点和全程保持夹持。", ("start", "shake", "settle")),
    }
    for task, (label, instruction, hint, stages) in definitions.items():
        plugin = TaskPlugin(task, "1.0.0", label, instruction, hint,
                            list(zip(stages, ("1 准备", "2 执行", "3 完成"))),
                            partial(make_environment, task), check_condition, partial(preflight, task),
                            interpolate, partial(contract, task),
                            prepare_recording=partial(prepare_recording, task),
                            source_sha256=sha, source_file=source.name, base_task="pick_place")
        plugins[task] = plugin
        catalog[task] = dict(label=label, text=instruction, hint=hint, phases=plugin.phases,
                             plugin=plugin.metadata())
