"""Single simulation owner; desktop GUI stays responsive during IK and recording."""
import json
import queue
import time
import traceback
from pathlib import Path
import numpy as np
from .plugin_api import physical_task
from .core import (make_environment,make_writer,preflight,expert,sample_condition,
                   validate_recipe,spatial_group)
from .tasks import TASKS,check_task_condition


def run(commands,events,frames,session,recipe):
    import cv2
    from contracts import evaluate
    # Preview frames are disposable; do not block process exit on an undrained
    # image queue when the parent window has already closed.
    frames.cancel_join_thread()
    env = None
    viewer = None
    viewer_data = None
    writer = None
    plan = None
    batch = None
    phase = "grasp"
    paused = True
    seed = int(recipe["seed"])
    seed_file = Path(session)/"seed_counter.json"
    if seed_file.exists():seed=max(seed,json.loads(seed_file.read_text())["next_seed"])

    def emit(kind,**values):events.put(dict(kind=kind,**values))

    def advance_seed():
        nonlocal seed
        current=seed
        seed+=1
        seed_file.write_text(json.dumps(dict(next_seed=seed)))
        return current

    def reset(cube,goal,current):
        nonlocal xyz,phase,paused
        env.reset_scene(cube,goal,current)
        xyz=env.data.site_xpos[env.tcp].copy()
        phase=TASKS[env.task_name]["phases"][0][0]
        paused=True
        start=env.start_xy if physical_task(env.task_name)=="wipe" else env.initial_cube[:2]
        group,split=spatial_group(start,env.goal_xy)
        emit("reset",seed=current,cube_xy=start.tolist(),goal_xy=env.goal_xy.tolist(),group=group,split=split)

    def finish():
        nonlocal writer,plan,paused
        if writer:
            if writer.labels:
                path=writer.finish(env,max_lift)
                emit("saved",path=str(path),success=writer.report.get("success",writer.report.get("pick_place_success",False)),frames=len(writer.labels))
            else:
                writer.abort()
                emit("message",text="没有录到帧，已放弃空记录")
            writer=None
        plan=None
        paused=True

    def start_next():
        nonlocal writer,plan,batch,max_lift,paused
        if not batch:return
        if batch["remaining"]<=0 or batch.get("stop"):
            emit("message",text="批采已完成/停止；失败轨迹保留供检查")
            batch=None
            emit("mode",value="idle")
            return
        attempts=1 if recipe.get("mode")=="fixed" else 20
        last_error=""
        for _ in range(attempts):
            current=advance_seed()
            try:
                cube,goal=sample_condition(recipe,current)
                reset(cube,goal,current)
                targets=preflight(env)
                writer=make_writer(session,env,"scripted",recipe)
                plan=iter(expert(env,targets))
                max_lift=float(env.cube_position()[2])
                paused=False
                emit("mode",value="batch",remaining=batch["remaining"])
                return
            except ValueError as exc:
                last_error=str(exc)
                with (Path(session)/"rejected_conditions.jsonl").open("a",encoding="utf-8") as f:
                    f.write(json.dumps(dict(seed=current,error=str(exc),recipe=recipe),ensure_ascii=False)+"\n")
                emit("message",text=f"seed {current} 未通过可达性预检，已记录并重新采样")
        batch=None
        emit("mode",value="idle")
        emit("error",text=("当前固定摆放未通过预检：" if attempts==1 else "连续20个随机条件未通过预检：")+last_error)

    try:
        env=make_environment(render=True,task=recipe.get("task","pick_place"),recipe=recipe)
        xyz=np.zeros(3)
        reset(recipe["cube_xy"],recipe["goal_xy"],advance_seed())
        max_lift=float(env.cube_position()[2])
        emit("ready")
        last_frame=0.
        last_progress=0.
        active=True
        while active:
            began=time.perf_counter()
            while True:
                try:c=commands.get_nowait()
                except queue.Empty:break
                try:
                    op=c["op"]
                    if op=="shutdown":active=False;break
                    if op=="viewer":
                        if viewer is not None:viewer.close();viewer=None
                        else:
                            import mujoco
                            import mujoco.viewer as mv
                            viewer_data=mujoco.MjData(env.model)
                            viewer=mv.launch_passive(env.model,viewer_data)
                            viewer.cam.lookat[:]=[.25,0,.08];viewer.cam.distance=1.65
                            viewer.cam.azimuth=135;viewer.cam.elevation=-25
                            viewer.opt.geomgroup[3]=0
                            emit("message",text="3D浏览器可旋转/缩放视角；使用独立数据副本，不向采集仿真施加鼠标扰动力")
                    elif op=="stop_batch":
                        if batch:batch["stop"]=True
                        emit("message",text="将在当前轨迹结束后停止批采")
                    elif op in ("reset","batch"):
                        if writer or batch:raise ValueError("先保存/放弃录制，或停止批采")
                        recipe=validate_recipe(c["recipe"])
                        if recipe["task"]!=env.task_name:raise ValueError("切换任务需新建对应任务场次")
                        if any(recipe.get(k,default)!=env.recipe_profile.get(k,default) for k,default in (("environment","basic"),("object","red_cube"))):
                            raise ValueError("切换场景/物品需新建场次")
                        seed=max(seed,int(recipe["seed"]))
                        if op=="reset":reset(recipe["cube_xy"],recipe["goal_xy"],advance_seed())
                        else:
                            batch=dict(remaining=int(recipe["count"]),stop=False)
                            start_next()
                    elif op=="move":
                        if batch:raise ValueError("批采中不能手动移动")
                        proposed=xyz+np.asarray(c["delta"])
                        env.action[:6]=env.ik(proposed,seed=env.action[:6])
                        xyz=proposed
                        paused=False
                    elif op=="gripper":
                        if batch:raise ValueError("批采中不能手动控制夹爪")
                        env.action[6]=c["aperture"]
                        paused=False
                    elif op=="phase":
                        if batch:raise ValueError("自动采集的阶段由专家轨迹标注")
                        phase=c["value"]
                    elif op=="record":
                        if batch:raise ValueError("批采中不能手动录制")
                        if writer:finish();emit("mode",value="idle")
                        else:
                            contacts,table=env.contact_evidence()
                            from .plugin_api import get_plugin
                            plugin=get_plugin(env.task_name)
                            if plugin and plugin.prepare_recording:plugin.prepare_recording(env)
                            elif physical_task(env.task_name)=="wipe":
                                if env.pad_contact()>.05:raise ValueError("请先抬离擦拭工具，再开始完整擦拭记录")
                                env.clear_wipe()
                            elif contacts.any() or not table:raise ValueError("请从尚未抓持、桌面承载的状态开始录制")
                            env.initial_cube=env.cube_position().copy()
                            if physical_task(env.task_name)!="wipe":env.start_xy=env.initial_cube[:2].copy()
                            check_task_condition(env.task_name,env.start_xy,env.goal_xy)
                            writer=make_writer(session,env,"keyboard",recipe)
                            max_lift=float(env.cube_position()[2]);paused=False
                            phase=TASKS[env.task_name]["phases"][0][0]
                            emit("mode",value="recording")
                    elif op=="discard":
                        if batch:raise ValueError("请用停止批采按钮")
                        if writer:writer.abort();writer=None
                        paused=True
                        emit("mode",value="idle")
                        emit("message",text="已放弃，partial诊断文件不参与导出")
                    else:raise ValueError("未知命令")
                except Exception as exc:emit("error",text=str(exc))
            if not active:break
            if not paused:
                observation=env.observe()
                try:
                    if plan:
                        try:action,stage=next(plan)
                        except StopIteration:
                            finish()
                            batch["remaining"]-=1
                            start_next()
                            continue
                    else:action,stage=env.action.copy(),phase
                    if writer:writer.append(observation,action,stage)
                    env.step(action)
                    max_lift=max(max_lift,float(env.cube_position()[2]))
                except Exception as exc:
                    if writer:
                        finish()
                        if batch:batch["remaining"]-=1
                    emit("error",text=f"轨迹中止：{type(exc).__name__}: {exc}")
                    if batch:start_next()
            now=time.perf_counter()
            if now-last_frame>=.10:
                if viewer is not None:
                    if viewer.is_running():
                        import mujoco
                        with viewer.lock():
                            viewer_data.qpos[:]=env.data.qpos;viewer_data.qvel[:]=env.data.qvel
                            mujoco.mj_forward(env.model,viewer_data)
                        viewer.sync()
                    else:viewer.close();viewer=None
                obs=env.observe()
                payload=dict(images=[cv2.imencode(".png",cv2.cvtColor(obs[k],cv2.COLOR_RGB2BGR),[cv2.IMWRITE_PNG_COMPRESSION,1])[1].tobytes() for k in ("overview","wrist")],
                    cube=(np.r_[env.start_xy,0.] if physical_task(env.task_name)=="wipe" else obs["cube_xyz"]).tolist(),tcp=obs["tcp_xyz"].tolist(),goal=env.goal_xy.tolist(),
                    wipe_fraction=float(obs.get("wipe_fraction",0.)),wipe_force=float(obs.get("wipe_force",0.)),
                    state=obs["state"].tolist(),phase=phase if not plan else stage if "stage" in locals() else "准备",time=obs["timestamp"],frames=len(writer.labels) if writer else 0)
                try:frames.put_nowait(payload)
                except queue.Full:pass
                last_frame=now
            # Manual interaction is20Hz; auto still records every20Hz sim frame
            # but can run faster than real time. Rendering never advances physics.
            if not batch:time.sleep(max(0,.05-(time.perf_counter()-began)))
    except BaseException:
        emit("fatal",text=traceback.format_exc())
    finally:
        if writer:
            try:writer.abort()
            except Exception:pass
        if viewer:viewer.close()
        if env:env.close()
        emit("closed")
