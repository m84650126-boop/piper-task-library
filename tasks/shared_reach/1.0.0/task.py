"""A portable task with external scene config and bundled MJCF/texture assets."""
from pathlib import Path
import json
import numpy as np
from workbench.plugin_api import TaskPlugin

API_VERSION = 1
HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE / "scene.json").read_text(encoding="utf-8"))


def make_environment(recipe, render, recorded_xml):
    import xml.etree.ElementTree as ET
    from workbench.core import ROOT, make_environment as base
    from workbench.scene_assets import compose
    from workbench.task_packages import resolve_asset_references
    source = recorded_xml
    if not source:
        xml = ET.fromstring(compose((ROOT / "generated/scene.xml").read_text(encoding="utf-8"), recipe))
        marker = ET.parse(HERE / "assets/marker.xml").getroot()
        for section in ("asset", "worldbody"):
            for element in marker.find(section):
                xml.find(section).append(element)
        source = ET.tostring(xml, encoding="unicode")
    env = base(render, "pick_place", resolve_asset_references(source, HERE), recipe)
    env.source_xml = source
    env.task_name = "shared_reach"
    env.recipe_profile = recipe
    return env


def check(start, goal):
    if np.linalg.norm(np.asarray(goal) - start) < .06:
        raise ValueError("Reference object and goal must be at least 6 cm apart")


def preflight(env):
    result = []
    q = env.action[:6].copy()
    z = CONFIG["target_height_m"]
    for xyz, seconds, stage in (([.36, -.05, .22], .5, "approach"), ([*env.goal_xy, z], 2., "reach"), ([*env.goal_xy, z], .7, "settle")):
        q = env.ik(xyz, seed=q)
        result.append((np.r_[q, .09], seconds, stage))
    return result


def expert(env, plan):
    for target, seconds, stage in plan:
        start = env.action.copy()
        n = round(seconds * 20)
        for i in range(n):
            u = (i + 1) / n
            yield start + (target - start) * u * u * (3 - 2 * u), stage


def evaluate(samples, stages, start, goal):
    tail = samples[-11:]
    stable = len(tail) == 11 and all(np.linalg.norm(s["tcp_xyz"][:2] - goal) < .01 and abs(s["tcp_xyz"][2] - CONFIG["target_height_m"]) < .01 for s in tail)
    untouched = all(np.linalg.norm(s["cube_xyz"][:2] - start[:2]) < .005 and not s["finger_contacts"].any() for s in samples)
    checks = dict(reach_phase="reach" in stages, stable_half_second=bool(stable), reference_untouched=bool(untouched))
    return dict(contract_version="shared_reach_v1", execution_unit="shared_reach", checks=checks,
                success=all(checks.values()), independent_learned_policy_validated=False)


def get_task():
    return TaskPlugin(id="shared_reach", version="1.0.0", label="共享任务示例 · 到达目标",
        text="Move the empty gripper above the green target and hold without touching the reference object.",
        hint="共享任务包示例：包含独立场景配置、MJCF装饰资产和纹理；尚未验证学习策略。",
        phases=[("approach", "1 准备"), ("reach", "2 到达"), ("settle", "3 稳定")],
        preset=CONFIG["preset"], make_environment=make_environment, check_condition=check,
        preflight=preflight, expert=expert, evaluate=evaluate)
