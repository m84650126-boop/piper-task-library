"""Simulation-only feedback execution against the local chunk service.

Truth is used only for evaluation annotations/termination, never policy inputs.
The simulation pauses during synchronous inference: this is not a real-time test.
"""
import argparse
import base64
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sim import PiperEnv, EpisodeWriter, TASK
from contracts import evaluate, THRESHOLDS
try:
    from .execution_manager import ExecutionManager
except ImportError:
    from execution_manager import ExecutionManager


def stable_held(samples):
    count = round(THRESHOLDS["hold_seconds"]/.05)+1
    window = samples[-count:]
    if len(window) < count:
        return False
    if not all(s["finger_contacts"].all() and s["cube_xyz"][2] > .06 for s in window):
        return False
    rel = np.asarray([s["cube_in_tcp"] for s in window])
    rotations = np.asarray([s["cube_rotation_in_tcp"] for s in window])
    angles = np.arccos(np.clip((np.trace(rotations @ rotations[-1].T,axis1=1,axis2=2)-1)/2,-1,1))
    return bool(np.linalg.norm(rel-rel[-1],axis=1).max() <= .008 and angles.max() <= .15)


def request(base, path, obj, timeout=1.):
    payload = json.dumps(obj).encode()
    req = urllib.request.Request(base+path, data=payload, headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def evaluation_phase(phase, samples, goal_xy=(.34,.18)):
    obs = samples[-1]
    held = stable_held(samples)
    if phase == "grasp" and held:
        return "move_held_object"
    if (phase == "move_held_object" and held and (np.abs(obs["cube_xyz"][:2]-goal_xy)<.045).all()
            and obs["cube_xyz"][2] > .12):
        return "place"
    return phase


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--condition", choices=("test", "new_position"), required=True)
    p.add_argument("--protocol", type=Path, default=Path("protocol"))
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--service", default="http://127.0.0.1:8765")
    p.add_argument("--policy-noise-seed", type=int, default=101)
    p.add_argument("--checkpoint-id", required=True, help="SHA256 of evaluated checkpoint weights")
    a = p.parse_args()
    if a.output.exists():
        p.error("output exists; use a separate directory for each condition/noise seed")
    protocol = json.loads((a.protocol / "protocol.json").read_text())
    seeds = [r["seed"] for r in protocol["records"]["test"]] if a.condition == "test" else list(range(5000,5030))
    a.output.mkdir(parents=True)
    env = PiperEnv(render=True)
    results = []
    try:
        for seed in seeds:
            env.reset(seed=seed)
            manager = ExecutionManager(env.bounds)
            generation = manager.reset()
            session = request(a.service, "/reset", {"policy_noise_seed":a.policy_noise_seed})["session"]
            writer = EpisodeWriter(a.output / "raw",env,seed,source="learned_policy",policy_id=a.checkpoint_id)
            phase, request_id, times, latency_e2e = "grasp", 0, [], []
            begin = time.perf_counter()
            max_lift = float(env.cube_position()[2])
            reason = "step_budget"
            success = False
            try:
                for step in range(400):
                    obs = env.observe()
                    phase = evaluation_phase(phase,writer.samples+[obs])
                    if not manager.queue:
                        query = dict(session=session,request=request_id,state=obs["state"].tolist(),task=TASK)
                        for raw, key in (("overview","camera1"),("wrist","camera2")):
                            query[key] = base64.b64encode(obs[raw].tobytes()).decode()
                        start = time.perf_counter()
                        response = request(a.service,"/predict",query)
                        elapsed = time.perf_counter()-start
                        if response["session"] != session or response["request"] != request_id:
                            raise RuntimeError("stale service response")
                        manager.accept(response["actions"],generation,elapsed)
                        request_id += 1
                        times.append(response["inference_s"])
                        latency_e2e.append(elapsed)
                    action = manager.next_action()
                    writer.append(obs,action,phase)
                    env.step(action)
                    max_lift = max(max_lift,float(env.cube_position()[2]))
                    report = evaluate(writer.samples+[env.observe()],writer.labels)
                    if report["pick_place_success"]:
                        success, reason = True, "success"
                        break
            except KeyboardInterrupt:
                manager.stop(intervention=True)
                writer.abort()
                raise
            except Exception as e:
                reason = type(e).__name__+": "+str(e)
                if isinstance(e, TimeoutError):
                    manager.counters["timeouts"] += int(manager.counters["timeouts"] == 0)
            finally:
                manager.stop()
                try:
                    request(a.service,"/reset",{})
                except Exception:
                    pass
            path = writer.finish(env,max_lift) if writer.labels else None
            if path is None:
                writer.abort()
            result = dict(condition=a.condition,reset_seed=seed,policy_noise_seed=a.policy_noise_seed,
                checkpoint_sha256=a.checkpoint_id,success=success,termination=reason,
                protocol_sha256=hashlib.sha256((a.protocol/"protocol.json").read_bytes()).hexdigest(),
                scene_sha256=hashlib.sha256((Path(__file__).resolve().parent.parent/"generated/scene.xml").read_bytes()).hexdigest(),
                task_sim_s=float(env.data.time), task_wall_s=time.perf_counter()-begin,
                inference_p50_s=float(np.quantile(times,.5)) if times else None,
                inference_p95_s=float(np.quantile(times,.95)) if times else None,
                inference_samples_s=times,roundtrip_samples_s=latency_e2e,
                mode="synchronous_paused_simulation",real_time_deadline_misses=None,
                **manager.counters,raw_file=str(path) if path else None)
            results.append(result)
            with (a.output/"trials.jsonl").open("a") as f:
                f.write(json.dumps(result)+"\n")
            print(json.dumps(result),flush=True)
    finally:
        env.close()
    (a.output/"summary.json").write_text(json.dumps(dict(trials=len(results),successes=sum(r["success"] for r in results),
        completion_rate=sum(r["success"] for r in results)/len(results) if results else None),indent=2))


if __name__ == "__main__":
    main()
