"""Multi-task recorded contract checks and exact physical replay."""
import hashlib
import json
from pathlib import Path
import h5py
import numpy as np
from .plugin_api import physical_task
from .tasks import TASKS,SURFACE_EXTRA,WIPE_EXTRA,evaluate_task,check_task_condition
from .data import scene_metadata


def extras(task):
    from .plugin_api import get_plugin
    plugin=get_plugin(task)
    return tuple(plugin.evidence_keys) if plugin else SURFACE_EXTRA+(WIPE_EXTRA if task=="wipe" else ())


def evidence(f):
    task=str(f.attrs["execution_unit"])
    keys=dict(state="observation/state",cube_xyz="sim/cube_xyz",tcp_xyz="sim/tcp_xyz",timestamp="timestamp")
    keys.update({key:"diagnostics/"+key for key in ("finger_contacts","table_contact","held_over_interval",
        "cube_velocity","cube_in_tcp","cube_rotation_in_tcp")+extras(task)})
    arrays={key:f[path][:] for key,path in keys.items()}
    samples=[{key:array[i] for key,array in arrays.items()} for i in range(len(f["action"]))]
    samples.append({key:f["final_observation/"+key][()] for key in keys})
    return samples


def inspect(path,require_images=True):
    with h5py.File(path,"r") as f:
        if f.attrs.get("status")!="complete" or not f.attrs.get("success"):return None,"incomplete or failed task"
        meta=scene_metadata(f);task=str(f.attrs["execution_unit"])
        check_task_condition(task,meta["start_xy"],meta["goal_xy"])
        from .scene_assets import task_text
        assert task in TASKS and task!="pick_place" and f.attrs["task"]==task_text(task,meta["recipe"].get("object","red_cube"))
        n=len(f["action"]);assert n>0 and f.attrs["fps"]==20
        for key in ("action","observation/state"):
            assert f[key].shape==(n,7) and np.isfinite(f[key][:]).all()
        for key in ("overview","wrist"):
            if not require_images and "observation/images/"+key not in f:continue
            image=f["observation/images/"+key]
            assert image.shape==(n,240,320,3) and image.dtype==np.uint8 and image[n//2].std()>5
        def aligned(name,obj):
            if isinstance(obj,h5py.Dataset) and not name.startswith("final_observation/") and name!="sim/final_qpos":
                assert obj.shape[0]==n,"unaligned "+name
        f.visititems(aligned)
        assert np.allclose(np.diff(f["timestamp"][:]),.05,atol=1e-8,rtol=0)
        assert f.attrs["action_semantics"]=="absolute_joint_targets_rad_and_aperture_m"
        assert f.attrs["observation_timing"]=="observation_before_action"
        stages=f["stage"].asstr()[:].tolist();labels=f["candidate_skill"].asstr()[:].tolist()
        assert labels==[task]*n
        samples=evidence(f)
        assert abs(samples[-1]["timestamp"]-samples[-2]["timestamp"]-.05)<1e-8
        report=evaluate_task(samples,stages,task,meta["start_xy"],meta["goal_xy"])
        assert report["success"] and report==json.loads(f.attrs["contract_report"]),"stored task contract differs"
        return dict(source_file=Path(path).name,frames=n,seed=int(f.attrs["seed"]),execution_unit=task,
            source=str(f.attrs["source"]),policy_id=str(f.attrs["policy_id"]),task=str(f.attrs["task"]),
            stages=stages,candidate_skills=labels,contract_report=report,
            skill_registry=json.loads(f.attrs["skill_registry"]),**meta),None


def validate(path,require_images=True,progress=None):
    import mujoco
    from .core import make_environment
    info,reason=inspect(path,require_images)
    if reason:raise ValueError(reason)
    with h5py.File(path,"r") as f:
        assert f.attrs["mujoco_version"]==mujoco.__version__
        env=make_environment(False,info["execution_unit"],str(f.attrs["scene_xml"]),recipe=info["recipe"])
        try:
            env.reset_scene(info["start_xy"],info["goal_xy"],info["seed"])
            states=f["sim/integration_state"][:];final=f["final_observation/integration_state"][:]
            actions=f["action"][:];positions=f["sim/qpos"][:];last=f["sim/final_qpos"][:]
            mujoco.mj_setState(env.model,env.data,states[0],env.state_spec);mujoco.mj_forward(env.model,env.data)
            env.held_over_interval=bool(f["diagnostics/held_over_interval"][0])
            from .plugin_api import get_plugin
            plugin=get_plugin(info["execution_unit"])
            if plugin and plugin.restore_initial:plugin.restore_initial(env,evidence(f)[0])
            if physical_task(info["execution_unit"])=="wipe":
                env.wipe_mask=f["diagnostics/wipe_mask"][0].copy()
                env.wipe_contact_s=float(f["diagnostics/wipe_contact_s"][0])
                env.wipe_distance=float(f["diagnostics/wipe_distance_m"][0])
                env.wipe_peak=float(f["diagnostics/wipe_peak_force"][0])
                env.last_pad_xy=env.data.geom_xpos[env.pad_geom,:2].copy()
            diag={key:f["diagnostics/"+key][:] for key in extras(info["execution_unit"])}
            probe=mujoco.MjData(env.model);worst=0.
            for i in range(len(actions)):
                if progress and i%80==0:progress(f"{info['execution_unit']}物理回放 {i}/{len(actions)}")
                mujoco.mj_setState(env.model,probe,states[i+1] if i+1<len(actions) else final,env.state_spec)
                applied=np.r_[probe.ctrl[:6],probe.ctrl[6]*2]
                assert np.array_equal(applied.astype(np.float32),actions[i])
                env.step(applied)
                error=float(np.max(np.abs(env.data.qpos-(positions[i+1] if i+1<len(actions) else last))))
                worst=max(worst,error);assert error<1e-7,(i,error)
                obs=env.observe()
                for key in diag:
                    target=diag[key][i+1] if i+1<len(actions) else f["final_observation/"+key][()]
                    np.testing.assert_allclose(obs[key],target,atol=1e-9,rtol=0,err_msg=key)
            return dict(path=str(path),seed=info["seed"],frames=info["frames"],success=True,
                source_sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest(),execution_unit=info["execution_unit"],
                contracts=info["contract_report"],replay=[dict(passed=True,start_frame=0,max_qpos_error=worst,
                    all_contact_and_coverage_diagnostics_match=True)])
        finally:env.close()
