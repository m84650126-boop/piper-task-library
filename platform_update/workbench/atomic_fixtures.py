"""Passive, contact-driven benchmark fixtures with no object actuators."""
from functools import partial
from pathlib import Path
import hashlib
import xml.etree.ElementTree as ET

import numpy as np

from .atomic_pick import interpolate, plan
from .plugin_api import TaskPlugin


TRAVEL = .07
BUTTON_TRAVEL = .014
TURN = np.pi / 3
FIXTURES = {
    "open": ("打开滑盖", "Open the sliding cover and leave it open."),
    "close": ("关闭滑盖", "Close the sliding cover and leave it closed."),
    "press": ("按压弹簧按钮", "Depress the spring-loaded button, then release it."),
    "rotate": ("旋转限位旋钮", "Rotate the knob clockwise to its mechanical stop, then let go."),
}
EVIDENCE = ("fixture_position", "fixture_velocity", "fixture_finger_force")


def geom(body, name, size, position, color, **kwargs):
    ET.SubElement(body, "geom", name=name, type="box", size=size, pos=position,
                  rgba=color, contype="1", conaffinity="2", **kwargs)


def add_fixture(root, task):
    world = root.find("worldbody")
    stand = ET.SubElement(world, "body", name="fixture", pos="0 0 0")
    moving = ET.SubElement(stand, "body", name="fixture_moving")
    if task == "press":
        geom(stand, "fixture_base", ".048 .046 .022", "0 0 .022", ".19 .23 .28 1")
        moving.set("pos", "0 0 .082")
        ET.SubElement(moving, "inertial", pos="0 0 0", mass=".035",
                      diaginertia=".000012 .000013 .000023")
        ET.SubElement(moving, "joint", name="fixture_joint", type="slide", axis="0 0 -1",
                      range=f"0 {BUTTON_TRAVEL}", stiffness="650", damping="2")
        geom(moving, "fixture_handle", ".032 .031 .008", "0 0 0", ".92 .33 .13 1", mass=".035")
    elif task == "rotate":
        geom(stand, "fixture_base", ".052 .052 .018", "0 0 .018", ".19 .23 .28 1")
        moving.set("pos", "0 0 .064")
        ET.SubElement(moving, "inertial", pos="0 0 0", mass=".09",
                      diaginertia=".000011 .000027 .000033")
        ET.SubElement(moving, "joint", name="fixture_joint", type="hinge", axis="0 0 -1",
                      range=f"0 {TURN}", frictionloss=".045", damping=".035")
        geom(moving, "fixture_handle", ".029 .014 .018", "0 0 0", ".28 .70 .59 1",
             friction="1.35 .02 .002")
    else:
        geom(stand, "fixture_base", ".092 .052 .015", "0 0 .015", ".19 .23 .28 1")
        geom(stand, "fixture_well", ".025 .033 .001", "-.035 0 .031", ".09 .11 .15 1")
        moving.set("pos", f"{-TRAVEL / 2} 0 .043")
        ET.SubElement(moving, "inertial", pos="0 0 .0055", mass=".13",
                      diaginertia=".000079 .000055 .000095")
        ET.SubElement(moving, "joint", name="fixture_joint", type="slide", axis="1 0 0",
                      range=f"0 {TRAVEL}", frictionloss=".55", damping="3")
        geom(moving, "fixture_lid", ".032 .042 .006", "0 0 0", ".40 .54 .63 1", mass=".10")
        geom(moving, "fixture_handle", ".015 .017 .015", "0 0 .024", ".93 .57 .15 1",
             mass=".03", friction="1.3 .015 .002")
    return root


def make_environment(task, recipe, render, recorded_xml):
    import mujoco
    from .core import ROOT, make_environment as make_base
    from .scene_assets import compose

    source = recorded_xml
    if not source:
        root = ET.fromstring(compose((ROOT / "generated/scene.xml").read_text(encoding="utf-8"), recipe))
        source = ET.tostring(add_fixture(root, task), encoding="unicode")
    env = make_base(render, "pick_place", source, recipe)
    env.task_name = task
    joint = env.model.joint("fixture_joint").id
    qpos = env.model.jnt_qposadr[joint]
    qvel = env.model.jnt_dofadr[joint]
    stand = env.model.body("fixture").id
    handle = env.model.geom("fixture_handle").id
    reset_base, observe_base, snapshot_base = env.reset_scene, env.observe, env.snapshot

    def observe():
        obs = observe_base()
        force = np.zeros(6)
        normal = 0.
        for i, contact in enumerate(env.data.contact):
            first, second = contact.geom
            if first == handle and env.model.geom_bodyid[second] in env.finger_bodies or (
                second == handle and env.model.geom_bodyid[first] in env.finger_bodies
            ):
                mujoco.mj_contactForce(env.model, env.data, i, force)
                normal += max(0., force[0])
        obs.update(fixture_position=float(env.data.qpos[qpos]),
                   fixture_velocity=float(env.data.qvel[qvel]), fixture_finger_force=normal)
        return obs

    def reset_scene(cube, goal, seed=0):
        env.model.body_pos[stand, :2] = goal
        reset_base(cube, goal, seed)
        if task == "close":
            env.data.qpos[qpos] = TRAVEL
        mujoco.mj_forward(env.model, env.data)
        return observe()

    def snapshot():
        root = ET.fromstring(snapshot_base())
        root.find("worldbody/body[@name='fixture']").set("pos", " ".join(map(str, env.model.body_pos[stand])))
        return ET.tostring(root, encoding="unicode")

    env.observe, env.reset_scene, env.snapshot = observe, reset_scene, snapshot
    return env


def check_condition(start, goal):
    if abs(start[0] - goal[0]) < .14 and abs(start[1] - goal[1]) < .085:
        raise ValueError("参考方块与工装之间需留出操作间隙")


def preflight(task, env):
    x, y = env.goal_xy
    if task == "rotate":
        from scipy.spatial.transform import Rotation
        xyz = [x, y, .065]
        targets = plan(env, [([x, y, .20], .09, 1.2, "approach"),
                             (xyz, .09, 1.35, "approach"), (xyz, 0., .85, "operate")])
        q = targets[-1][0][:6]
        for angle in np.linspace(0., TURN, 9)[1:]:
            rotation = Rotation.from_rotvec([0., 0., -angle]).as_matrix() @ env.down
            q = env.ik(xyz, seed=q, rotation=rotation)
            targets.append((np.r_[q, 0.], .28, "operate"))
        targets.append((np.r_[q, .09], .65, "retreat"))
        q = env.ik([x, y, .20], seed=q, rotation=rotation)
        return targets + [(np.r_[q, .09], 1.35, "retreat"), (np.r_[q, .09], .65, "retreat")]
    if task == "press":
        steps = [([x, y, .20], 0., 1.2, "approach"), ([x, y, .095], 0., 1.2, "approach"),
                 ([x, y, .089], 0., 1., "operate"), ([x, y, .089], 0., .45, "operate"),
                 ([x, y, .20], 0., 1.3, "retreat"), ([x, y, .20], 0., .65, "retreat")]
    else:
        sign = 1 if task == "open" else -1
        start, end = x - sign * TRAVEL / 2, x + sign * TRAVEL / 2
        steps = [([start, y, .20], .09, 1.2, "approach"),
                 ([start, y, .069], .09, 1.4, "approach"),
                 ([start, y, .069], 0., .85, "operate")]
        # Short IK segments keep the handle at constant height along the rail.
        steps.extend(([px, y, .069], 0., .35, "operate") for px in np.linspace(start, end, 8)[1:])
        steps.extend([([end, y, .069], .09, .65, "retreat"),
                      ([end, y, .20], .09, 1.2, "retreat"), ([end, y, .20], .09, .65, "retreat")])
    return plan(env, steps)


def contract(task, samples, stages, start, goal, pick_profile=None):
    position = np.array([s["fixture_position"] for s in samples])
    force = np.array([s["fixture_finger_force"] for s in samples])
    initial = TRAVEL if task == "close" else 0.
    final = {"open": TRAVEL, "rotate": TURN}.get(task, 0.)
    direction = -1 if task == "close" else 1
    contact_progress = float(np.maximum(direction * np.diff(position), 0.)[force[1:] > .2].sum())
    excursion = {"press": BUTTON_TRAVEL, "rotate": TURN}.get(task, TRAVEL)
    tolerance, max_velocity = (.055, .07) if task == "rotate" else (.004, .015)
    tail = samples[-11:]
    checks = dict(
        initial_fixture_position=bool(abs(position[0] - initial) < .003 and force[0] < .05),
        contact_driven_travel=bool(contact_progress >= .8 * excursion),
        measured_excursion=bool(np.max(direction * (position - initial)) >= .85 * excursion),
        force_bounded=bool(force.max() < 100.),
        final_fixture_still=bool(len(tail) == 11 and all(
            abs(s["fixture_position"] - final) < tolerance and abs(s["fixture_velocity"]) < max_velocity
            and s["fixture_finger_force"] < .05 and s["tcp_xyz"][2] > .14 for s in tail)),
        reference_cube_untouched=bool(all(not s["finger_contacts"].any() and
            np.linalg.norm(s["cube_xyz"][:2] - start) < .005 for s in samples)),
    )
    return dict(contract_version=f"piper_fixture_{task}_v1", execution_unit=task,
                checks=checks, success=all(checks.values()),
                **({"contact_travel_rad": contact_progress} if task == "rotate"
                   else {"contact_travel_m": contact_progress}),
                peak_sampled_normal_force_n=float(force.max()),
                independent_learned_policy_validated=False)


def register(catalog, plugins):
    source = Path(__file__)
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    phases = [("approach", "1 接近"), ("operate", "2 操作"), ("retreat", "3 退开")]
    preset = dict(goal_xy=[.37, .11], goal_bounds=[.33, .41, .08, .16],
                  cube_xy=[.40, -.10], cube_bounds=[.36, .44, -.16, -.09])
    for task, (label, instruction) in FIXTURES.items():
        hint = "目标坐标定位工装。成功依据被动关节行程、夹指接触力和退开后的稳定状态。"
        if task == "rotate":
            hint = "夹持旋钮顺时针转动60°；用专家采集，或用Q/E键旋转夹爪。"
        plugin = TaskPlugin(task, "1.0.0", label, instruction, hint, phases,
                            partial(make_environment, task), check_condition, partial(preflight, task),
                            interpolate, partial(contract, task), preset=preset, evidence_keys=EVIDENCE,
                            source_sha256=sha, source_file=source.name)
        plugins[task] = plugin
        catalog[task] = dict(label=label, text=instruction, hint=hint, phases=phases, preset=preset,
                             plugin=plugin.metadata())
