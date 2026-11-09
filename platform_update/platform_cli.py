"""Portable public-platform entry points: doctor, task plugins, collection and GUI."""
import argparse
import hashlib
import importlib.metadata as md
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parent


def plugin_command(args):
    from workbench.plugin_api import read_plugin,ROOT as root,API_VERSION
    directory=root/"task_plugins";index=directory/"enabled.json"
    data=json.loads(index.read_text(encoding="utf-8")) if index.exists() else dict(api_version=API_VERSION,plugins=[])
    if args.action!="disable":
        from workbench.tasks import TASKS
    if args.action=="list":
        print(json.dumps({key:{k:v for k,v in item.items() if k in ("label","hint","plugin")} for key,item in TASKS.items()},indent=2,ensure_ascii=False));return
    if args.action=="disable":
        data["plugins"]=[item for item in data["plugins"] if item["id"]!=args.value]
    else:
        source=Path(args.value).resolve();plugin=read_plugin(source)
        if plugin.id in TASKS:
            existing=TASKS[plugin.id].get("plugin",{})
            if existing.get("source_sha256")==plugin.source_sha256:
                print("Task already enabled:",plugin.id);return
            raise ValueError("Task id exists. Preserve the old version; disable before installing a changed version.")
        directory.mkdir(exist_ok=True)
        target=directory/(plugin.id+".py")
        if target.exists() and target.read_bytes()!=source.read_bytes():
            raise ValueError("Existing plugin file differs; preserve/rename it before replacing")
        if target!=source:shutil.copy2(source,target)
        data["plugins"].append(dict(id=plugin.id,file=target.name,version=plugin.version,sha256=plugin.source_sha256))
    directory.mkdir(exist_ok=True)
    tmp=index.with_suffix(".tmp");tmp.write_text(json.dumps(data,indent=2),encoding="utf-8");tmp.replace(index)
    print("Plugin configuration saved. Restart the workbench to apply.")


def doctor(args):
    from workbench.runtime import data_home,conversion_python
    from workbench.scene_assets import verify_assets
    from workbench.tasks import TASKS
    checks={};errors=[]
    for package in ("mujoco","numpy","scipy","opencv-python","h5py"):
        try:checks[package]=md.version(package)
        except md.PackageNotFoundError:errors.append("Missing "+package)
    checks["asset_manifest_sha256"]=verify_assets()
    directory=data_home();directory.mkdir(parents=True,exist_ok=True)
    checks["data_home"]=str(directory);checks["data_home_writable"]=os.access(directory,os.W_OK)
    if not checks["data_home_writable"]:errors.append("Data directory is not writable")
    checks["conversion_python"]=str(conversion_python());checks["conversion_installed"]=conversion_python().is_file()
    try:import tkinter;checks["tkinter"]=tkinter.TkVersion
    except ImportError:errors.append("Tkinter unavailable; install Python Tk bindings for desktop UI")
    if args.physics or args.render:
        from workbench.core import make_environment
        env=make_environment(args.render,recipe={"environment":"robosuite_lab","object":"rs_can"})
        try:
            obs=env.reset_scene([.4,-.1],[.34,.18],1234);env.step(env.action)
            checks["physics"]=dict(nq=env.model.nq,geometries=env.model.ngeom,state_shape=list(obs["state"].shape))
            if args.render:checks["rgb_shapes"]={k:list(obs[k].shape) for k in ("overview","wrist")}
        finally:env.close()
    result=dict(status="passed" if not errors else "failed",platform=platform.platform(),python=sys.version,
        executable=sys.executable,root=str(ROOT),checks=checks,errors=errors,tasks=list(TASKS))
    if args.output:Path(args.output).write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result,indent=2,ensure_ascii=False))
    if errors:raise SystemExit(1)


def collect(args):
    from workbench.core import DEFAULT,new_session,make_environment,sample_condition,preflight,expert,make_writer,validate_recipe
    from workbench.tasks import TASKS
    if args.task not in TASKS:raise ValueError("Unknown/disabled task")
    recipe={**DEFAULT,**TASKS[args.task].get("preset",{}),"task":args.task,"count":args.episodes,"seed":args.seed,
            "mode":"random" if args.random else "fixed"}
    if args.environment:recipe["environment"]=args.environment
    if args.object:recipe["object"]=args.object
    if args.recipe:recipe.update(json.loads(Path(args.recipe).read_text(encoding="utf-8")))
    if recipe["task"]!=args.task:raise ValueError("Recipe task must match --task")
    recipe=validate_recipe(recipe);session=new_session(recipe)
    if args.diagnostics_only:(session/"DIAGNOSTICS_ONLY.txt").write_text("No RGB; not a training dataset")
    env=make_environment(not args.diagnostics_only,args.task,recipe=recipe);results=[]
    try:
        for i in range(int(recipe["count"])):
            seed=int(recipe["seed"])+i;c,g=sample_condition(recipe,seed);env.reset_scene(c,g,seed)
            targets=preflight(env);writer=make_writer(session,env,"scripted",recipe);lift=0.
            try:
                for action,stage in expert(env,targets):
                    writer.append(env.observe(),action,stage);env.step(action);lift=max(lift,float(env.cube_position()[2]))
                path=writer.finish(env,lift)
            except BaseException:
                writer.abort();raise
            success=bool(writer.report.get("success",writer.report.get("pick_place_success",False)))
            results.append(dict(path=str(path),success=success,seed=seed))
            print(json.dumps(results[-1]),flush=True)
    finally:env.close()
    (session/"collection_summary.json").write_text(json.dumps(dict(task=args.task,episodes=results),indent=2))
    print("SESSION "+str(session),flush=True)


def main():
    p=argparse.ArgumentParser();subs=p.add_subparsers(dest="command",required=True)
    d=subs.add_parser("doctor");d.add_argument("--physics",action="store_true");d.add_argument("--render",action="store_true");d.add_argument("--output")
    ext=subs.add_parser("plugins");ext.add_argument("action",choices=("list","enable","disable"));ext.add_argument("value",nargs="?")
    pkg=subs.add_parser("packages",help="Inspect/export/install versioned tasks with scene assets")
    pkg.add_argument("action",choices=("seal","export","inspect","install"));pkg.add_argument("source")
    pkg.add_argument("--output");pkg.add_argument("--enable",action="store_true",help="Trust this Python task and enable on next launch")
    library=subs.add_parser("library",help="Synchronize and install from the shared Git task catalog")
    library.add_argument("action",choices=("refresh","list","install"))
    library.add_argument("--repository",default="https://github.com/m84650126-boop/piper-task-library.git")
    library.add_argument("--branch",default="main");library.add_argument("--task");library.add_argument("--version")
    library.add_argument("--enable",action="store_true")
    c=subs.add_parser("collect");c.add_argument("--task",required=True);c.add_argument("--episodes",type=int,default=1)
    c.add_argument("--seed",type=int,default=1000000);c.add_argument("--random",action="store_true");c.add_argument("--recipe")
    c.add_argument("--environment");c.add_argument("--object");c.add_argument("--diagnostics-only",action="store_true")
    subs.add_parser("skills",help="List requested atomic skills and their collection availability")
    subs.add_parser("launch")
    a=p.parse_args()
    if a.command=="doctor":doctor(a)
    elif a.command=="plugins":plugin_command(a)
    elif a.command=="collect":collect(a)
    elif a.command=="skills":
        from workbench.skill_catalog import entries
        from workbench.tasks import TASKS
        print(json.dumps(entries(TASKS),indent=2,ensure_ascii=True))
    elif a.command=="packages":
        from workbench.task_packages import package_command
        package_command(a)
    elif a.command=="library":
        from workbench.task_library import library_command
        library_command(a)
    else:
        from collection_workbench import main as launch
        sys.argv=[sys.argv[0]];launch()


if __name__=="__main__":main()
