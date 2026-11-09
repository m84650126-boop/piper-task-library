"""Scene recipes, explicit reset, physical experts and reversible data management."""
from pathlib import Path
from datetime import datetime
import hashlib
import json
import math
import os
import time
import xml.etree.ElementTree as ET
import h5py
import numpy as np
from .plugin_api import physical_task

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = "piper_workbench_raw_v1"
from .tasks import TASKS,check_task_condition
DEFAULT = dict(cube_xy=[.40,-.10], goal_xy=[.34,.18],
    cube_bounds=[.34,.44,-.16,-.04], goal_bounds=[.28,.40,.08,.20],
    min_distance=.10, seed=42000, count=100, grid_m=.05, split_seed=20261105,
    mode="random", fixed_orientation=True,task="pick_place",environment="basic",object="red_cube")


def validate_recipe(recipe):
    r = {**DEFAULT, **recipe}
    if r["task"] not in TASKS:raise ValueError("未知采集任务")
    from .plugin_api import plugin_metadata
    plugin=plugin_metadata(r["task"])
    if plugin:
        if r.get("task_plugin") and r["task_plugin"]!=plugin:raise ValueError("任务代码版本与场次配置不一致")
        r["task_plugin"]=plugin
    from .scene_assets import ENVIRONMENTS,OBJECTS
    if r["environment"] not in ENVIRONMENTS or r["object"] not in OBJECTS:raise ValueError("未知场景或物品")
    if "height_m" in OBJECTS[r["object"]] and physical_task(r["task"])!="pick_place":
        raise ValueError("较大尺寸物品当前用于完整拾放，请为其他任务选择原有尺寸")
    if physical_task(r["task"])=="wipe" and r["object"]!="red_cube":raise ValueError("擦拭使用已安装工具，物品选择仅适用于拾放/推拉")
    for key in ("cube_xy", "goal_xy"):
        a = np.asarray(r[key],dtype=float)
        if a.shape != (2,) or not np.isfinite(a).all():
            raise ValueError(f"{key}: 必须是两个有限坐标（米）")
        if not (.18 <= a[0] <= .55 and -.30 <= a[1] <= .30):
            raise ValueError(f"{key}: 编辑范围 X=0.18~0.55、Y=-0.30~0.30 米；每点仍需验证可达性")
        r[key] = a.tolist()
    for key in ("cube_bounds", "goal_bounds"):
        a = np.asarray(r[key],dtype=float)
        if a.shape != (4,) or not np.isfinite(a).all():
            raise ValueError(f"{key}: 需要 xmin,xmax,ymin,ymax")
        if not (.18 <= a[0] < a[1] <= .55 and -.30 <= a[2] < a[3] <= .30):
            raise ValueError(f"{key}: 区域上下界错误或超出编辑范围")
        r[key] = a.tolist()
    if not .06 <= float(r["min_distance"]) <= .5:
        raise ValueError("起点到目标的最小距离应在0.06~0.5米")
    if int(r["count"]) < 1 or int(r["count"]) > 10000 or int(r["seed"]) < 0:
        raise ValueError("数量需在1~10000，seed必须非负")
    if r["grid_m"] != .05 or r["split_seed"] != 20261105:
        raise ValueError("本版本空间分组固定为5cm网格和split_seed=20261105")
    if r["mode"] not in ("random","fixed"):
        raise ValueError("未知采样模式")
    return r


def spatial_group(cube_xy,goal_xy):
    # Absolute 5cm cells: changing a sampling rectangle cannot change the split.
    cells = np.floor((np.round(np.r_[cube_xy,goal_xy],5)+1e-10)/.05).astype(int).tolist()
    group = "_".join(map(str,cells))
    bucket = int(hashlib.sha256(("20261105:"+group).encode()).hexdigest()[:8],16)%100
    return group, "train" if bucket < 80 else "validation" if bucket < 90 else "test"


def condition_id(cube_xy,goal_xy):
    return hashlib.sha256(np.round(np.r_[cube_xy,goal_xy],5).astype("<f8").tobytes()).hexdigest()


def sample_condition(recipe,seed):
    r = validate_recipe(recipe)
    if r["mode"] == "fixed":
        cube,goal = np.array(r["cube_xy"]),np.array(r["goal_xy"])
    else:
        rng = np.random.default_rng(seed)
        for _ in range(100):
            c,g = r["cube_bounds"],r["goal_bounds"]
            cube = rng.uniform([c[0],c[2]],[c[1],c[3]])
            goal = rng.uniform([g[0],g[2]],[g[1],g[3]])
            if np.linalg.norm(cube-goal) >= r["min_distance"]:
                try:check_task_condition(r["task"],cube,goal)
                except ValueError:continue
                break
        else:
            raise ValueError("两个采样区域无法满足最小距离，请调整范围")
    if np.linalg.norm(cube-goal) < r["min_distance"]:
        raise ValueError("物体与目标过近")
    check_task_condition(r["task"],cube,goal)
    return cube,goal


def new_session(recipe):
    recipe = validate_recipe(recipe)
    from .runtime import data_home
    folder = data_home()/"workbench"/(datetime.now().strftime("session_%Y%m%d_%H%M%S_")+str(time.time_ns()%1000000))
    folder.mkdir(parents=True,exist_ok=False)
    (folder/"raw").mkdir()
    (folder/"recipe.json").write_text(json.dumps(recipe,indent=2),encoding="utf-8")
    return folder


def safe_episode(session,name):
    session = Path(session).resolve()
    if Path(name).name != name or not name.startswith("episode_") or not name.endswith(".h5"):
        raise ValueError("非法轨迹文件名")
    p = (session/"raw"/name).resolve()
    if p.parent != (session/"raw").resolve():
        raise ValueError("轨迹超出当前采集场次")
    return p


def trash_episode(session,name):
    path = safe_episode(session,name)
    trash = Path(session)/"trash"
    trash.mkdir(exist_ok=True)
    target = trash/(str(time.time_ns())+"_"+name)
    path.rename(target)
    return target


def catalog(session):
    rows = []
    if not session:
        return rows
    for path in sorted((Path(session)/"raw").glob("episode_*.h5")):
        if ".partial." in path.name:
            continue
        try:
            with h5py.File(path,"r") as f:
                if f.attrs.get("status") != "complete":
                    continue
                rows.append(dict(name=path.name,seed=int(f.attrs["seed"]),success=bool(f.attrs["success"]),
                    frames=len(f["action"]),cube_xy=np.asarray(f.attrs.get("start_xy",f.attrs["initial_cube_xyz"][:2])).tolist(),
                    task=str(f.attrs.get("execution_unit","pick_place")),
                    goal_xy=np.asarray(f.attrs["goal_xy"]).tolist(),split=str(f.attrs["planned_split"]),
                    group=str(f.attrs["spatial_group"]),condition_id=str(f.attrs["condition_id"]),
                    source=str(f.attrs["source"])))
        except (OSError,KeyError):
            continue
    return rows


def make_environment(render=True,task="pick_place",recorded_xml=None,recipe=None):
    recipe={**DEFAULT,**(recipe or {}),"task":task}
    from .plugin_api import get_plugin
    plugin=get_plugin(task)
    if plugin:
        env=plugin.make_environment(recipe,render,recorded_xml)
        if env.task_name!=task:raise ValueError("Plugin environment task_name mismatch")
        return env
    if task!="pick_place":
        from .surface import SurfaceEnv
        return SurfaceEnv(task,render,recorded_xml,recipe)
    from .scene_assets import compose,object_height_offset,pick_place_profile
    source=recorded_xml or compose((ROOT/"generated/scene.xml").read_text(encoding="utf-8"),recipe)
    from sim import PiperEnv
    class SceneEnv(PiperEnv):
        task_name="pick_place"
        def snapshot(self):
            root=ET.fromstring(self.source_xml)
            root.find("worldbody/site[@name='goal']").set("pos",f"{self.goal_xy[0]} {self.goal_xy[1]} 0.001")
            return ET.tostring(root,encoding="unicode")
        def reset_scene(self,cube_xy,goal_xy,seed=0):
            import mujoco
            self.goal_xy = np.asarray(goal_xy,dtype=float)
            self.model.site_pos[self.model.site("goal").id,:2] = self.goal_xy
            # Original reset gives exactly the original robot initialization.
            super().reset(seed,randomize=False)
            self.data.qpos[self.cube_q:self.cube_q+3] = np.r_[cube_xy,.021+object_height_offset(recipe)]
            self.data.qvel[self.cube_dof:self.cube_dof+6] = 0
            mujoco.mj_forward(self.model,self.data)
            for _ in range(200):self._substep()
            mujoco.mj_forward(self.model,self.data)
            self.data.time = 0
            self.initial_cube = self.cube_position().copy()
            self.start_xy = self.initial_cube[:2].copy()
            self.held_over_interval = False
            self.reset_seed = seed
            return self.observe()
    env=SceneEnv(render=render,scene_xml=source,pick_profile=pick_place_profile(recipe["object"]))
    env.source_xml=source;env.recipe_profile=recipe
    return env


def waypoints(env):
    from .scene_assets import object_height_offset
    c,g = env.initial_cube[:2],env.goal_xy
    contact_z=.029+object_height_offset(env.recipe_profile)
    if env.recipe_profile.get("object") == "rs_cereal_native":
        # Grip the tall carton above its center so its top clears the palm.
        contact_z += .020
    return [(np.r_[c,.23],.09,1.2,"approach"),(np.r_[c,contact_z],.09,1.6,"descend"),
        (np.r_[c,contact_z],0.,1.,"close"),(np.r_[c,.23],0.,1.6,"lift"),
        (np.r_[g,.23],0.,2.,"transfer"),(np.r_[g,contact_z],0.,1.5,"lower"),
        (np.r_[g,contact_z],.09,1.,"release"),(np.r_[g,.23],.09,1.5,"retreat"),
        (np.r_[g,.23],.09,.8,"settle")]


def preflight(env):
    from .plugin_api import get_plugin
    plugin=get_plugin(env.task_name)
    if plugin:return plugin.preflight(env)
    if env.task_name!="pick_place":
        from .surface import preflight as plan_surface
        return plan_surface(env)
    guess = env.action[:6].copy()
    targets = []
    for xyz,aperture,seconds,stage in waypoints(env):
        guess = env.ik(xyz,seed=guess)
        targets.append((np.r_[guess,aperture],seconds,stage))
    return targets


def expert(env,targets):
    from .plugin_api import get_plugin
    plugin=get_plugin(env.task_name)
    if plugin:
        yield from plugin.expert(env,targets)
        return
    if env.task_name!="pick_place":
        from .surface import trajectory
        yield from trajectory(env,targets)
        return
    from sim import FPS
    for target,seconds,stage in targets:
        start = env.action.copy()
        for i in range(round(seconds*FPS)):
            u=(i+1)/round(seconds*FPS)
            yield start+(target-start)*(u*u*(3-2*u)),stage


def make_writer(session,env,source,recipe):
    from sim import EpisodeWriter
    class SceneWriter(EpisodeWriter):
        def __init__(self):
            super().__init__(Path(session)/"raw",env,env.reset_seed,source=source,policy_id="workbench_expert_v1" if source=="scripted" else "keyboard")
            self.file.attrs["schema_version"] = SCHEMA
            root = ET.fromstring(self.file.attrs["scene_xml"])
            root.find("worldbody/site[@name='goal']").set("pos",f"{env.goal_xy[0]} {env.goal_xy[1]} 0.001")
            snapshot = env.snapshot() if hasattr(env,"snapshot") else ET.tostring(root,encoding="unicode")
            start=env.start_xy if physical_task(env.task_name)=="wipe" else env.initial_cube[:2]
            group,split = spatial_group(start,env.goal_xy)
            self.file.attrs.update(scene_xml=snapshot,scene_sha256=hashlib.sha256(snapshot.encode()).hexdigest(),
                initial_cube_xyz=env.initial_cube,goal_xy=env.goal_xy,spatial_group=group,planned_split=split,
                condition_id=condition_id(start,env.goal_xy),session_id=Path(session).name,
                recipe_json=json.dumps(recipe),task=TASKS[env.task_name]["text"],execution_unit=env.task_name,
                start_xy=start,start_semantics="wipe_strip_start" if physical_task(env.task_name)=="wipe" else "initial_object_center")
            from .scene_assets import object_info,uses_assets,manifest,task_text
            from .plugin_api import plugin_metadata
            plugin=plugin_metadata(env.task_name)
            if plugin:self.file.attrs["task_plugin_json"]=json.dumps(plugin)
            self.file.attrs.update(environment=recipe.get("environment","basic"),
                object_profile_json=json.dumps(object_info(recipe.get("object","red_cube"))),
                upstream_asset_manifest_sha256=manifest()[1] if uses_assets(recipe) else "",
                task=task_text(env.task_name,recipe.get("object","red_cube")))
            if env.task_name!="pick_place":
                self.file.attrs["skill_registry"]=json.dumps(dict(execution_unit=env.task_name,status="scripted_data_collection",
                    independent_learned_policy_validated=False,scope=TASKS[env.task_name]["hint"]))

        def append(self,obs,action,stage):
            super().append(obs,action,stage)
            if env.task_name=="pick_place":return
            from .task_data import extras
            self.labels[-1]=env.task_name
            self.datasets["candidate_skill"][-1]=env.task_name
            for key in extras(env.task_name):
                name="diagnostics/"+key;value=np.asarray(obs[key])
                if name not in self.datasets:
                    self.datasets[name]=self.file.create_dataset(name,shape=(0,)+value.shape,maxshape=(None,)+value.shape,
                        dtype=value.dtype,chunks=True)
                ds=self.datasets[name];ds.resize(ds.shape[0]+1,axis=0);ds[-1]=value

        def finish(self,environment,max_lift):
            if env.task_name=="pick_place":return super().finish(environment,max_lift)
            from .tasks import evaluate_task
            if not self.labels:raise ValueError("不能保存空轨迹")
            final=env.observe()
            stages=self.file["stage"].asstr()[:].tolist()
            self.report=evaluate_task(self.samples+[final],stages,env.task_name,self.file.attrs["start_xy"],env.goal_xy,pick_profile=self.pick_profile)
            for key,value in final.items():
                if key not in ("overview","wrist","qpos"):
                    self.file.create_dataset("final_observation/"+key,data=value)
            self.file.create_dataset("sim/final_qpos",data=env.data.qpos)
            self.file.attrs.update(status="complete",success=self.report["success"],contract_report=json.dumps(self.report),
                final_cube_xyz=env.cube_position(),max_cube_z=max_lift)
            self.file.close()
            path=self.partial.with_name(self.partial.name.replace(".partial.h5",".h5"));self.partial.rename(path)
            return path
    return SceneWriter()
