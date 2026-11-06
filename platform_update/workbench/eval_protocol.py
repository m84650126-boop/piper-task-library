"""Freeze simulation conditions before inference and aggregate measured trials."""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
import numpy as np
from .core import ROOT,validate_recipe,sample_condition
from .plugin_api import physical_task


def sha_file(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda:stream.read(4*1024*1024),b""):h.update(block)
    return h.hexdigest()


def validate_spec(spec):
    s={**dict(backend="local",checkpoint=str(ROOT/"pretrained_model"),device="cpu",trials=1,max_steps=400,prefix=10,
        request_timeout_s=60.,load_timeout_s=300.,pace=True,policy_seed=101,cohort="custom_unclassified"),**spec}
    if s["backend"] not in ("local","http","expert"):raise ValueError("未知评测后端")
    s["recipe"]=validate_recipe(s["recipe"])
    if s["backend"]!="expert" and physical_task(s["recipe"]["task"])!="pick_place":
        raise ValueError("模型闭环当前支持完整拾放及其配置变体；其他任务的策略终止适配尚未验证，可先做专家自检")
    for key,lo,hi in (("trials",1,1000),("max_steps",1,2000),("prefix",1,50)):
        if isinstance(s[key],bool) or int(s[key])!=float(s[key]) or not lo<=int(s[key])<=hi:raise ValueError(key+"超出支持范围")
        s[key]=int(s[key])
    if not 0<float(s["request_timeout_s"])<=300 or not 1<=float(s["load_timeout_s"])<=900:raise ValueError("推理/加载超时参数无效")
    if int(s["policy_seed"])<0:raise ValueError("策略seed必须非负")
    if s["device"]!="cpu" and not str(s["device"]).startswith("cuda"):raise ValueError("推理设备需为cpu或cuda")
    if s["backend"]=="local":
        folder=Path(s["checkpoint"]).expanduser().resolve()
        for name in ("config.json","model.safetensors","policy_preprocessor.json","policy_postprocessor.json"):
            if not (folder/name).is_file():raise ValueError("模型目录缺少文件："+name)
        config=json.loads((folder/"config.json").read_text(encoding="utf-8"))
        if config.get("type") not in ("smolvla","pi05"):raise ValueError("当前推理后端支持LeRobot SmolVLA或pi05配置")
        expected={"observation.state":[7],"observation.images.camera1":[3,240,320],"observation.images.camera2":[3,240,320]}
        if {k:v["shape"] for k,v in config["input_features"].items()}!=expected or config["output_features"]["action"]["shape"]!=[7]:
            raise ValueError("模型输入/动作维度不符合当前7维PiPER和双相机映射")
        if s["prefix"]>int(config["chunk_size"]):raise ValueError("执行前缀超过模型动作块长度")
        s["checkpoint"]=str(folder)
    if s["backend"]=="http":
        url=urlsplit(s.get("service",""))
        if url.scheme not in ("http","https") or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("推理服务需为不含凭据的HTTP/HTTPS地址")
    return s


def freeze_conditions(spec):
    recipe=spec["recipe"];conditions=[]
    if spec.get("conditions_file"):
        raw=json.loads(Path(spec["conditions_file"]).read_text(encoding="utf-8"))["conditions"]
        if len(raw)!=spec["trials"]:raise ValueError("固定清单条数必须与试验数相同")
    else:raw=[dict(seed=int(recipe["seed"])+i,policy_noise_seed=int(spec["policy_seed"])+i) for i in range(spec["trials"])]
    for i,item in enumerate(raw):
        seed=int(item["seed"])
        if seed<0:raise ValueError("场景seed必须非负")
        noise=int(item.get("policy_noise_seed",int(spec["policy_seed"])+i))
        if noise<0:raise ValueError("策略噪声seed必须非负")
        if "start_xy" in item and "goal_xy" in item:
            r={**recipe,"mode":"fixed","cube_xy":item["start_xy"],"goal_xy":item["goal_xy"]}
        else:r=recipe
        start,goal=sample_condition(r,seed)
        conditions.append(dict(trial=i+1,seed=seed,policy_noise_seed=noise,
            start_xy=start.tolist(),goal_xy=goal.tolist(),cohort=spec["cohort"]))
    return conditions


def quantiles(values):
    return dict(p50_s=float(np.quantile(values,.5)),p95_s=float(np.quantile(values,.95)),samples=len(values)) if values else dict(p50_s=None,p95_s=None,samples=0)


def summary(trials,planned,backend,status):
    completed=[r for r in trials if not r.get("interrupted")]
    latencies=[v for r in trials for v in r.get("inference_samples_s",[])]
    roundtrips=[v for r in trials for v in r.get("roundtrip_samples_s",[])]
    successes=sum(bool(r["success"]) for r in completed)
    return dict(status=status,backend=backend,expert_is_not_learned_policy=backend=="expert",planned_trials=planned,
        attempted_trials=len(trials),completed_trials=len(completed),successes=successes,failures=len(completed)-successes,
        interrupted_trials=len(trials)-len(completed),completion_rate=successes/len(completed) if completed else None,
        inference=quantiles(latencies),roundtrip=quantiles(roundtrips),first_request_included=True,
        mean_task_sim_s=float(np.mean([r["task_sim_s"] for r in completed])) if completed else None,
        mean_task_wall_s=float(np.mean([r["task_wall_s"] for r in completed])) if completed else None,
        peak_allocated_bytes=max((r["peak_allocated_bytes"] for r in trials if r.get("peak_allocated_bytes") is not None),default=None),
        counters={key:sum(r.get(key,0) for r in trials) for key in ("timeouts","queue_underruns","interventions","constraint_violations")},
        mode="synchronous_paused_simulation",real_time_deadline_validation=False,
        policy_inputs=["observation.state","observation.images.camera1","observation.images.camera2","task"],
        camera_mapping=dict(camera1="overview",camera2="wrist"))
