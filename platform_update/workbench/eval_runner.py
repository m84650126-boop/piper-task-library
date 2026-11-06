"""Independent simulator owner for live, auditable closed-loop evaluation."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys
import time
import traceback
import numpy as np
from .core import ROOT,make_environment,make_writer,preflight,expert
from .tasks import evaluate_task
from .plugin_api import physical_task
from .scene_assets import task_text
from .eval_protocol import validate_spec,freeze_conditions,sha_file,summary,quantiles
from .eval_policy import PolicyClient,InterruptedEvaluation
from a100.execution_manager import ExecutionManager
from a100.closed_loop import evaluation_phase


def emit(kind,**data):
    print("WB_EVENT "+json.dumps(dict(kind=kind,**data),ensure_ascii=True),flush=True)


def frame(obs,trial,phase,step):
    import cv2
    images=[]
    for key in ("overview","wrist"):
        ok,png=cv2.imencode(".png",cv2.cvtColor(obs[key],cv2.COLOR_RGB2BGR))
        if not ok:raise ValueError("Failed encoding camera image")
        images.append(base64.b64encode(png).decode())
    emit("frame",images=images,trial=trial,phase=phase,step=step,sim_s=float(obs["timestamp"]),
        state=obs["state"].tolist(),cube_xyz=obs["cube_xyz"].tolist(),tcp_xyz=obs["tcp_xyz"].tolist())


def run(spec,folder):
    folder=Path(folder).resolve();folder.mkdir(parents=True,exist_ok=True)
    stopped=lambda:(folder/"STOP_REQUESTED").exists()
    trials=[];env=None;client=None;writer=None;status="error";error=None;condition_sha=None
    try:
        s=validate_spec(spec);conditions=freeze_conditions(s)
        condition_sha=hashlib.sha256(json.dumps(conditions,sort_keys=True).encode()).hexdigest()
        (folder/"trials.jsonl").touch(exist_ok=False)
        (folder/"summary.json").write_text(json.dumps(summary([],len(conditions),s["backend"],"starting"),indent=2),encoding="utf-8")
        (folder/"effective_config.json").write_text(json.dumps(s,indent=2),encoding="utf-8")
        frozen=dict(schema="piper_sim_evaluation_v1",conditions=conditions,condition_set_sha256=condition_sha,spec=s,
            source_hashes={name:sha_file(ROOT/name) for name in ("contracts.py","sim.py","workbench/tasks.py","workbench/core.py","workbench/eval_runner.py",
                "workbench/eval_protocol.py","workbench/eval_policy.py","workbench/plugin_api.py","a100/execution_manager.py","a100/closed_loop.py","a100/inference_service.py")},
            mode="synchronous_paused_simulation",learned_policy_supported_execution_unit="pick_place",
            control_dt_s=.05,execution_prefix=s["prefix"],interpolation="none; absolute targets at20Hz; no clipping",
            success_criteria="existing task physical contracts; stable held and stable released support for full pick_place",
            cohort_is_user_supplied_not_proven_generalization=True)
        protocol_bytes=json.dumps(frozen,sort_keys=True,indent=2).encode()
        (folder/"frozen_protocol.json").write_bytes(protocol_bytes);protocol_sha=hashlib.sha256(protocol_bytes).hexdigest()
        emit("status",text="测试条件已固定，正在初始化仿真")
        recipe=s["recipe"];task=recipe["task"]
        env=make_environment(True,task,recipe=recipe)
        first=conditions[0];obs=env.reset_scene(first["start_xy"],first["goal_xy"],first["seed"])
        frame(obs,1,"初始化",0)
        if s["backend"]!="expert":
            client=PolicyClient(s,folder,stopped,emit);client.start()
        for condition in conditions:
            if stopped():raise InterruptedEvaluation()
            trial=condition["trial"];begin=time.perf_counter();times=[];roundtrips=[];peaks=[];steps=0
            manager=ExecutionManager(env.bounds,prefix=1 if s["backend"]=="expert" else s["prefix"],timeout_s=float(s["request_timeout_s"]))
            generation=manager.reset();reason="step_budget";interrupted=False;success=False;raw=None;scene_sha=None;stages=[];report=None
            emit("trial_start",condition=condition)
            try:
                obs=env.reset_scene(condition["start_xy"],condition["goal_xy"],condition["seed"])
                frame(obs,trial,"grasp" if physical_task(task)=="pick_place" else "approach",0)
                if client:client.reset(condition["policy_noise_seed"])
                plan=iter(expert(env,preflight(env))) if s["backend"]=="expert" else None
                identity=client.description.get("checkpoint_sha256","") if client else "expert_diagnostic_not_a_model"
                writer=make_writer(folder,env,"learned_policy" if client else "scripted",recipe)
                writer.file.attrs.update(policy_id=identity,dataset_role="evaluation_only",evaluation_protocol_sha256=protocol_sha)
                scene_sha=str(writer.file.attrs["scene_sha256"]);phase="grasp";last_frame=0.;lift=float(env.cube_position()[2])
                for step in range(s["max_steps"]):
                    if stopped():raise InterruptedEvaluation()
                    tick=time.perf_counter();obs=env.observe()
                    if plan is not None:
                        try:action,phase=next(plan)
                        except StopIteration:reason="expert_ended";break
                        manager.accept(np.asarray(action).reshape(1,7),generation,0.)
                    else:
                        phase=evaluation_phase(phase,writer.samples+[obs],env.goal_xy)
                        if not manager.queue:
                            frame(obs,trial,phase,step)
                            emit("inference_start",trial=trial,step=step)
                            response,elapsed=client.predict(obs,task_text(task,recipe["object"]))
                            manager.accept(response["actions"],generation,elapsed)
                            server_latency=float(response["inference_s"])
                            if not np.isfinite(server_latency) or server_latency<0:raise ValueError("Invalid inference timing")
                            times.append(server_latency);roundtrips.append(elapsed)
                            if response.get("peak_allocated_bytes") is not None:peaks.append(int(response["peak_allocated_bytes"]))
                            emit("inference_done",trial=trial,step=step,inference_s=server_latency,roundtrip_s=elapsed,
                                action_chunk_length=len(response["actions"]),prefix=s["prefix"],stats=quantiles(times))
                    if stopped():raise InterruptedEvaluation()
                    action=manager.next_action();writer.append(obs,action,phase);stages.append(phase)
                    env.step(action);steps+=1;lift=max(lift,float(env.cube_position()[2]));after=env.observe()
                    report=evaluate_task(writer.samples+[after],stages,task,condition["start_xy"],env.goal_xy)
                    success=bool(report.get("success",report.get("pick_place_success",False)))
                    if time.perf_counter()-last_frame>=.10:
                        frame(after,trial,phase,steps);last_frame=time.perf_counter()
                    if success:reason="success";break
                    if s["pace"]:time.sleep(max(0.,.05-(time.perf_counter()-tick)))
            except InterruptedEvaluation:
                interrupted=True;reason="user_interruption";success=False;manager.stop(intervention=True)
            except Exception as exc:
                reason=type(exc).__name__+": "+str(exc);success=False
                if isinstance(exc,TimeoutError) and not manager.counters["timeouts"]:manager.counters["timeouts"]+=1
                emit("status",text="本次试验终止："+reason)
            finally:
                manager.stop()
                if writer:
                    if writer.labels:
                        path=writer.finish(env,lift);raw=path.relative_to(folder).as_posix()
                        if not interrupted:success=bool(writer.report.get("success",writer.report.get("pick_place_success",False)))
                    else:writer.abort()
                    writer=None
            result=dict(**condition,success=bool(success),termination=reason,interrupted=interrupted,steps=steps,
                task_sim_s=float(env.data.time),task_wall_s=time.perf_counter()-begin,
                inference_samples_s=times,roundtrip_samples_s=roundtrips,inference=quantiles(times),roundtrip=quantiles(roundtrips),
                protocol_sha256=protocol_sha,scene_sha256=scene_sha,raw_file=raw,contract_report=report,
                checkpoint_sha256=client.description.get("checkpoint_sha256") if client else None,
                peak_allocated_bytes=max(peaks) if peaks else None,
                **manager.counters)
            trials.append(result)
            with (folder/"trials.jsonl").open("a",encoding="utf-8") as stream:stream.write(json.dumps(result)+"\n")
            current=summary(trials,len(conditions),s["backend"],"running")
            (folder/"summary.json").write_text(json.dumps(current,indent=2),encoding="utf-8")
            frame(env.observe(),trial,"完成" if success else reason,steps);emit("trial_done",result=result,summary=current)
            if interrupted:raise InterruptedEvaluation()
            if manager.counters["timeouts"]:raise TimeoutError("推理超时后已停止整批评测并清空动作队列")
        status="completed"
    except InterruptedEvaluation:status="interrupted"
    except Exception as exc:
        error=type(exc).__name__+": "+str(exc)
        (folder/"error.log").write_text(traceback.format_exc(),encoding="utf-8");emit("error",text=error)
    finally:
        if writer:writer.abort()
        if client:client.close()
        if env:env.close()
        result=summary(trials,int(spec.get("trials",0)),spec.get("backend","unknown"),status)
        result.update(error=error,folder=str(folder),condition_set_sha256=condition_sha)
        (folder/"summary.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
        emit("finished",summary=result)
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--config",type=Path,required=True);args=parser.parse_args()
    spec=json.loads(args.config.read_text(encoding="utf-8"));result=run(spec,args.config.resolve().parent)
    if result["status"]=="error":raise SystemExit(1)


if __name__=="__main__":main()
