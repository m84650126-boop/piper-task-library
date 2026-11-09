"""Pure data provenance checks, also used in the separate LeRobot environment."""
import hashlib
import json
import xml.etree.ElementTree as ET
import numpy as np
from .plugin_api import physical_task
from .core import spatial_group,condition_id


def scene_metadata(f):
    xml = str(f.attrs["scene_xml"])
    digest = hashlib.sha256(xml.encode()).hexdigest()
    if digest != f.attrs["scene_sha256"]:
        raise ValueError("场景快照hash错误")
    goal = np.asarray(f.attrs["goal_xy"],dtype=float)
    start = np.asarray(f.attrs["initial_cube_xyz"],dtype=float)
    root = ET.fromstring(xml)
    marker = np.fromstring(root.find("worldbody/site[@name='goal']").get("pos"),sep=" ")[:2]
    if not np.array_equal(goal,marker) or not np.allclose(start,f["sim/cube_xyz"][0],atol=1e-7):
        raise ValueError("实际起点/目标与场景记录不一致")
    anchor=np.asarray(f.attrs.get("start_xy",start[:2]),dtype=float)
    task=str(f.attrs.get("execution_unit","pick_place"))
    recipe=json.loads(f.attrs["recipe_json"])
    from .scene_assets import pick_place_profile
    if json.loads(f.attrs.get("pick_place_profile_json", "null")) != pick_place_profile(recipe.get("object", "red_cube")):
        raise ValueError("拾放物品判据与场次配置不一致")
    from .plugin_api import plugin_metadata
    plugin=plugin_metadata(task)
    if plugin:
        if json.loads(f.attrs.get("task_plugin_json","null"))!=plugin:raise ValueError("回放/转换需要采集时相同版本的任务插件")
    elif "task_plugin_json" in f.attrs:raise ValueError("任务插件未启用，无法重新判定数据")
    object_profile=None;asset_digest=""
    if "object_profile_json" in f.attrs:
        from .scene_assets import object_info,verify_assets,uses_assets
        object_profile=json.loads(f.attrs["object_profile_json"])
        if object_profile!=object_info(recipe.get("object","red_cube")):raise ValueError("物品模型参数与配置不一致")
        if uses_assets(recipe):asset_digest=verify_assets(str(f.attrs["upstream_asset_manifest_sha256"]))
    if recipe.get("task","pick_place")!=task:raise ValueError("任务与场次配置不一致")
    if physical_task(task)!="wipe" and not np.allclose(anchor,start[:2],atol=1e-7):raise ValueError("起点语义错误")
    if physical_task(task)=="wipe":
        if f.attrs.get("start_semantics")!="wipe_strip_start":raise ValueError("擦拭起点语义缺失")
        delta=goal-anchor;length=np.linalg.norm(delta)
        if length<=0:raise ValueError("擦拭带长度为零")
        direction=delta/length;normal=np.array([-direction[1],direction[0]])
        points=np.array([anchor+direction*a+normal*b for a in np.linspace(0,length,17) for b in np.linspace(-.03,.03,9)])
        recorded=np.array([np.fromstring(root.find(f"worldbody/site[@name='wipe_cell_{i}']").get("pos"),sep=" ")[:2] for i in range(153)])
        if not np.allclose(points,recorded,atol=1e-12,rtol=0):raise ValueError("擦拭带标记与实际起点/终点不一致")
        pad=root.find(".//geom[@name='wipe_pad']")
        if pad is None or not np.array_equal(np.fromstring(pad.get("size"),sep=" "),[.035,.025,.006]):
            raise ValueError("擦拭工具几何不匹配")
    group,split = spatial_group(anchor,goal)
    identity = condition_id(anchor,goal)
    if (group != f.attrs["spatial_group"] or split != f.attrs["planned_split"] or identity != f.attrs["condition_id"]):
        raise ValueError("空间分组或实际条件hash不一致")
    return dict(raw_schema="piper_workbench_raw_v1",initial_cube_xyz=start.tolist(),start_xy=anchor.tolist(),goal_xy=goal.tolist(),
        scene_sha256=digest,spatial_group=group,planned_split=split,condition_id=identity,
        session_id=str(f.attrs["session_id"]),recipe=recipe,object_profile=object_profile,
        upstream_asset_manifest_sha256=asset_digest,environment=recipe.get("environment","basic"),task_plugin=plugin)
