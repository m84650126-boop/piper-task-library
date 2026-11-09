"""Task catalog and pure contracts. Importable in the conversion environment."""
import numpy as np

TASKS = {
    "pick_place":dict(label="完整拾放",text="Pick up the red cube and place it in the green target area.",
        phases=[("grasp","1 抓取"),("move_held_object","2 持物移动"),("place","3 放置")],
        hint="完整拾放；起点=方块，目标=放置中心。固定朝下姿态。"),
    "push":dict(label="桌面推送",text="Push the red cube along the table into the green target area, then disengage.",
        phases=[("approach","1 接近"),("push","2 推送"),("retreat","3 退开")],
        hint="起点=方块；向世界+X±30°推6~16cm。全程近桌面，退开后稳定。",
        preset=dict(cube_xy=[.36,-.1],goal_xy=[.46,-.1],cube_bounds=[.32,.38,-.13,-.07],goal_bounds=[.42,.48,-.13,-.07],min_distance=.06)),
    "pull":dict(label="夹持桌面拉回",text="Grasp the red cube, pull it along the table into the green target area, then release it.",
        phases=[("grasp","1 夹持"),("pull","2 拉回"),("release","3 释放")],
        hint="起点=方块；向世界-X±30°拉6~16cm。先稳定双指夹持，再近桌面拉回和释放。",
        preset=dict(cube_xy=[.42,-.1],goal_xy=[.32,-.1],cube_bounds=[.40,.46,-.13,-.07],goal_bounds=[.28,.34,-.13,-.07],min_distance=.06)),
    "wipe":dict(label="桌面擦拭（工具已安装）",text="Wipe the marked strip of the table with the mounted pad, then lift the pad clear of the table.",
        phases=[("approach","1 接近"),("wipe","2 接触擦拭"),("retreat","3 抬离")],
        hint="起点/目标=擦拭带两端；宽6cm、长6~16cm。工具已安装；接触覆盖≥90%，非污渍去除仿真。",
        preset=dict(cube_xy=[.34,-.08],goal_xy=[.44,-.08],cube_bounds=[.30,.36,-.13,-.03],goal_bounds=[.40,.46,-.13,-.03],min_distance=.06)),
}

SURFACE_EXTRA=("robot_object_contact","interval_robot_contact","interval_max_cube_z",
               "interval_supported_fraction","interval_max_bottom_clearance")
WIPE_EXTRA=("wipe_mask","wipe_fraction","wipe_contact","wipe_force","wipe_contact_s","wipe_distance_m","wipe_peak_force")


def check_task_condition(task,start,goal):
    from .plugin_api import get_plugin
    plugin=get_plugin(task)
    if plugin:return plugin.check_condition(np.asarray(start),np.asarray(goal))
    if task=="pick_place":return
    delta=np.asarray(goal)-np.asarray(start)
    distance=np.linalg.norm(delta)
    if not .06-1e-7<=distance<=.16+1e-7:
        raise ValueError("推/拉/擦拭当前支持端点距离6~16厘米，请调整坐标或区域")
    if task in ("push","pull"):
        sign=1 if task=="push" else -1
        if sign*delta[0]<=0 or abs(delta[1])>abs(delta[0])*np.tan(np.pi/6)+1e-7:
            raise ValueError("推送支持+X±30°，拉回支持-X±30°；超出当前已声明方向范围")


def evaluate_surface(samples,stages,task,start,goal):
    from atomic_contracts import evaluate_atomic
    # The established atomic checker is reused in a coordinate frame aligned
    # with this episode's start/goal. Only the distance-dependent checks change.
    start=np.asarray(start);goal=np.asarray(goal)
    delta=goal-start[:2];distance=float(np.linalg.norm(delta));direction=delta/distance
    transformed=[]
    base=np.asarray([1.,0.] if task=="push" else [-1.,0.])
    axis=np.array([[direction[0],direction[1]],[-direction[1],direction[0]]])
    # Old contract expects +/-X10cm; do not distort contacts/heights or replay.
    # Compute common physical evidence with an exactly10cm virtual goal, then
    # replace geometric progress checks using actual metres below.
    for s in samples:
        t=dict(s);p=np.asarray(s["cube_xyz"]).copy()
        p[:2]=(p[:2]-start[:2])@axis.T
        if task=="pull":p[:2]*=-1
        t["cube_xyz"]=p;transformed.append(t)
    report=evaluate_atomic(transformed,stages,task,[0,0,float(samples[0]["cube_xyz"][2])],base*.1)
    xy=np.array([s["cube_xyz"][:2] for s in samples])
    progress=(xy-start[:2])@direction
    driven=float(np.maximum(np.diff(progress),0)[np.array([s["interval_robot_contact"] for s in samples[1:]],bool)&(np.asarray(stages)==task)].sum())
    checks=report["checks"]
    tail=xy[-11:]
    # Preserve support/release/stability requirements; target error is evaluated
    # in the real world instead of the old fixed-direction coordinate frame.
    checks["directional_progress"]=bool(abs(progress[-1]-distance)<=.018)
    checks["target_reached"]=bool(np.linalg.norm(xy[-1]-goal)<.018)
    stable_tail=samples[-11:]
    checks["released_and_stable"]=bool(len(stable_tail)==11 and
        all(s["table_contact"] and not s["robot_object_contact"] for s in stable_tail) and
        max(np.linalg.norm(s["cube_velocity"][:3]) for s in stable_tail)<.02 and
        max(np.linalg.norm(s["cube_velocity"][3:]) for s in stable_tail)<.15 and
        np.linalg.norm(tail-goal,axis=1).max()<.018)
    checks["physical_contact_drives_motion"]=bool(driven>=.6*distance)
    report.update(contract_version="piper_workbench_surface_v1",success=all(checks.values()),
        progress_m=float(progress[-1]),contact_driven_progress_m=driven,target_error_m=float(np.linalg.norm(xy[-1]-goal)),
        thresholds={**report["thresholds"],"target_distance_m":distance,"min_contact_driven_progress_m":.6*distance},
        direction_xy=direction.tolist())
    return report


def evaluate_wipe(samples,stages,start,goal):
    distance=float(np.linalg.norm(np.asarray(goal)-np.asarray(start)[:2]))
    final=samples[-1];tail=samples[-11:]
    real_contacts=sum(bool(s["wipe_contact"]) and float(s["wipe_force"])>.05 for s in samples)
    masks=np.array([s["wipe_mask"] for s in samples],dtype=bool)
    fractions=np.array([s["wipe_fraction"] for s in samples],dtype=float)
    consistent=bool(masks.shape==(len(samples),153) and
        np.all(np.diff(masks.astype(int),axis=0)>=0) and np.allclose(fractions,masks.mean(axis=1),atol=1e-12,rtol=0))
    checks=dict(start_tool_clear=not bool(samples[0]["wipe_contact"]),
        physical_contact_samples=bool(real_contacts>=20),coverage_records_consistent=consistent,
        contact_coverage=bool(float(final["wipe_fraction"])>=.90),
        sustained_contact=bool(float(final["wipe_contact_s"])>=1.5),
        contact_motion=bool(float(final["wipe_distance_m"])>=1.5*distance),
        bounded_force=bool(float(final["wipe_peak_force"])<=40.),
        wiping_phase_present="wipe" in stages,
        lifted_clear=bool(len(tail)==11 and all(not s["wipe_contact"] and s["tcp_xyz"][2]>.15 for s in tail)))
    return dict(contract_version="piper_workbench_wipe_v1",execution_unit="wipe",checks=checks,success=all(checks.values()),
        coverage_fraction=float(final["wipe_fraction"]),contact_seconds=float(final["wipe_contact_s"]),
        contact_distance_m=float(final["wipe_distance_m"]),peak_contact_force_n=float(final["wipe_peak_force"]),
        thresholds=dict(min_coverage=.90,min_contact_s=1.5,min_contact_motion_m=1.5*distance,max_force_n=40.,
            grid_shape=[17,9],strip_width_m=.06,contact_force_threshold_n=.05),
        scope="mounted rigid wiping pad; contact footprint coverage proxy; no fluid/dirt removal model",
        independent_learned_policy_validated=False)


def evaluate_task(samples,stages,task,start,goal,labels=None,pick_profile=None):
    from .plugin_api import get_plugin
    plugin=get_plugin(task)
    if plugin:
        profile_args = {"pick_profile":pick_profile} if pick_profile else {}
        report=plugin.evaluate(samples,stages,np.asarray(start),np.asarray(goal),**profile_args)
        if not isinstance(report,dict) or type(report.get("success")) is not bool or report.get("execution_unit")!=task:
            raise ValueError("Plugin must return a boolean success and matching execution_unit")
        if not report.get("contract_version") or not isinstance(report.get("checks"),dict):raise ValueError("Plugin contract_version/checks required")
        return report
    if task=="pick_place":
        from contracts import evaluate,candidate_for
        return evaluate(samples,labels if labels is not None else [candidate_for(s) for s in stages],goal_xy=goal,pick_profile=pick_profile)
    if task in ("push","pull"):return evaluate_surface(samples,stages,task,start,goal)
    if task=="wipe":return evaluate_wipe(samples,stages,start,goal)
    raise ValueError("未知任务")


from .atomic_pick import register as register_atomic_pick
from .atomic_fixtures import register as register_fixtures
from .plugin_api import PLUGINS, load_enabled
register_atomic_pick(TASKS, PLUGINS)
register_fixtures(TASKS, PLUGINS)
load_enabled(TASKS)
