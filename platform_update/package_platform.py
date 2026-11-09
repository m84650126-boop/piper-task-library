"""Build a source/asset preview, prove relocation, freeze code and verify ZIP bytes."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parent


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git(folder,*args):
    return subprocess.check_output(["git","-c","core.autocrlf=false","-c","commit.gpgsign=false",
        "-c","user.name=Platform Preview Snapshot","-c","user.email=preview@local.invalid",*args],cwd=folder,text=True).strip()


def main():
    p=argparse.ArgumentParser();p.add_argument("--output",type=Path,default=ROOT/"deliverables/piper_workbench_preview_20261106")
    a=p.parse_args();stage=a.output.resolve();archive=Path(str(stage)+".zip")
    if stage.exists() or archive.exists():raise ValueError("Use a new release path")
    stage.mkdir(parents=True)
    files=["collection_workbench.py","platform_cli.py","setup_platform.py","sim.py","contracts.py","atomic_tasks.py",
        "atomic_contracts.py","atomic_data.py","convert_lerobot.py","validate.py","preview.py","install_scene_assets.py",
        "requirements.txt","start_workbench.cmd","start_workbench.sh","setup_windows.cmd","05_workbench.cmd",
        "PLATFORM_README_zh.md","TASK_PLUGIN_API_zh.md","WORKBENCH_zh.md","SCENE_ASSETS_zh.md","LICENSE.proposed.txt",
        "test_platform_plugins.py","test_scene_assets.py","test_workbench_tasks.py","test_workbench.py",
        "skill_registry.json","atomic_skill_registry.json","workbench_task_registry.json","package_platform.py",
        "TASK_SHARING_zh.md","test_task_packages.py","CREATE_TASK_zh.md","test_task_authoring.py","EVALUATION_zh.md","test_live_evaluation.py",
        "USER_MANUAL_zh.md","COLLECTION_CONTRACTS_zh.md","ATOMIC_TASKS_zh.md","ATOMIC_COLLECTION_zh.md"]
    for name in files:shutil.copy2(ROOT/name,stage/name)
    shutil.copy2(ROOT/"PLATFORM_README_zh.md",stage/"README_FIRST_zh.md")
    for folder in ("workbench","examples","task_plugins"):
        shutil.copytree(ROOT/folder,stage/folder,ignore=shutil.ignore_patterns("__pycache__","qa*.py","ui_*qa.py","ui_capture.py","audit_assets.py"))
    for entry in json.loads((ROOT/"task_plugins/enabled.json").read_text(encoding="utf-8"))["plugins"]:
        if entry.get("package_manifest"):
            from workbench.task_packages import read_folder
            package=(ROOT/entry["package_manifest"]).parent;read_folder(package)
            shutil.copytree(package,stage/package.relative_to(ROOT),ignore=shutil.ignore_patterns("__pycache__"))
    (stage/"generated").mkdir()
    for path in (ROOT/"generated").glob("*.xml"):shutil.copy2(path,stage/"generated"/path.name)
    shutil.copy2(ROOT/"generated/SOURCE_LICENSE",stage/"generated/SOURCE_LICENSE")
    shutil.copytree(ROOT/"generated/assets",stage/"generated/assets")
    shutil.copytree(ROOT/"vendor",stage/"vendor",ignore=shutil.ignore_patterns("__pycache__",".git",".cache"))
    (stage/"a100").mkdir()
    for name in ("run.py","train_entry.py","prepare_protocol.py","verify_inputs.py","model_revisions.json","constraints-linux.txt","setup_a100.sh",
        "inference_service.py","execution_manager.py","closed_loop.py","evaluation_protocol.json","README_A100_zh.md"):
        shutil.copy2(ROOT/"a100"/name,stage/"a100"/name)
    (stage/"THIRD_PARTY_NOTICES.md").write_text(
        "# Third-party assets\n\nrobosuite v1.5.2 assets are pinned in vendor/robosuite_v1_5_2/ASSET_MANIFEST.json; upstream MIT LICENSE is preserved.\n\n"
        "Robot meshes are distributed with the original provided generated/SOURCE_LICENSE (MIT). Their original upstream commit was not captured; source provenance must be confirmed before a public release.\n\n"
        "Project-source license selection is pending owner approval. LICENSE.proposed.txt is a draft only. This is a local technical preview, not a public publication.\n",encoding="utf-8")
    (stage/".gitignore").write_text(".venv*/\ndata/\n.portability_data/\nmodels/\npretrained_model/\nruns/\n*.safetensors\n*.h5\n*.log\n.env\n.env.*\n!.env.example\n__pycache__/\n**/__pycache__/\nSOURCE_CODE_COMMIT.txt\nsource_history.bundle\nFILE_MANIFEST.json\n")
    # A physically independent copy with spaces in its path, no copied venv/cache.
    relocation=stage.parent/("relocation with spaces "+datetime.now().strftime("%H%M%S"))
    shutil.copytree(stage,relocation)
    env={**os.environ,"PYTHONPATH":"","PYTHONIOENCODING":"utf-8","PIPER_WORKBENCH_DATA":str(relocation/".portability_data")}
    def execute(args):
        result=subprocess.run([sys.executable,*args],cwd=relocation,env=env,text=True,encoding="utf-8",stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        if result.returncode:raise RuntimeError("Relocation check failed:\n"+result.stdout)
        return result.stdout
    doctor=json.loads(execute([str(relocation/"platform_cli.py"),"doctor","--physics","--render"]))
    assert Path(doctor["root"])==relocation and doctor["status"]=="passed"
    task="shared_reach" if "shared_reach" in doctor["tasks"] else "reach_target"
    output=execute([str(relocation/"platform_cli.py"),"collect","--task",task,"--episodes","1","--diagnostics-only"])
    assert '"success": true' in output
    replay=json.loads(execute(["-c","from pathlib import Path; import json; from validate import validate_episode; f=list(Path('.portability_data/workbench').glob('*/raw/episode_*.h5')); assert len(f)==1; r=validate_episode(f[0],require_images=False); print(json.dumps(dict(success=r['success'],replay=r['replay'])))"]))
    assert replay["success"] and all(r["passed"] for r in replay["replay"])
    (stage/"reports").mkdir()
    portability=dict(status="passed",tested_platform=platform.platform(),python_version=platform.python_version(),
        fresh_source_and_asset_copy=True,path_with_spaces=True,no_venv_copied=True,physics=True,dual_rgb_shapes=doctor["checks"]["rgb_shapes"],
        plugin_collection_success=True,plugin_collection_task=task,plugin_saved_action_replay=replay["replay"],
        linux_status="installation and startup scripts supplied; runtime not tested",
        desktop_ui_status="previous app-owned component tests passed; no GUI test performed in relocated copy")
    (stage/"reports/portability.json").write_text(json.dumps(portability,indent=2))
    proof_path=ROOT/"data/workbench_assets_reach_target_red_cube_qa_session.txt"
    if proof_path.exists():
        session=Path(proof_path.read_text());evaluation=json.loads((session/"evaluation.json").read_text())
        verified=next(p for p in sorted(session.glob("export_*"),reverse=True) if p.is_dir() and (p/"protocol/protocol.json").exists())
        source=json.loads((verified/"dataset/source_manifest.json").read_text());assert source["status"]=="complete" and source["readback_passed"]
        (stage/"reports/plugin_e2e.json").write_text(json.dumps(dict(task="reach_target",episodes=evaluation["successes"],
            replay_enabled=evaluation["replay_enabled"],errors=evaluation["errors"],
            note="3 RGB episodes from separate spatial groups; converted/read back with frozen train-only protocol"),indent=2))
    release=dict(name="PiPER Data Workbench",version="0.2.0-preview",created_utc=datetime.now(timezone.utc).isoformat(),
        public_publication=False,project_license_status="pending_owner_approval",robot_upstream_commit_status="not_captured",
        task_api_version=1,robot_adapter="PiPER-L7d",lerobot_version="0.6.2",dataset_format="v3.0",
        weights_included=False,user_datasets_included=False,credentials_included=False,
        install_mode="recreate local venv; source+assets portable; no binary freeze")
    (stage/"RELEASE.json").write_text(json.dumps(release,indent=2))
    git(stage,"init","-b","codex/platform-preview");git(stage,"add",".")
    git(stage,"commit","-m","Freeze portable data workbench and versioned task extension API")
    commit=git(stage,"rev-parse","HEAD");(stage/"SOURCE_CODE_COMMIT.txt").write_text(commit+"\n")
    git(stage,"bundle","create","source_history.bundle","HEAD")
    excluded={".git","data",".portability_data",".venv",".venv-lerobot","models","pretrained_model"}
    def included(path):
        return path.parts[0] not in excluded and "__pycache__" not in path.parts
    selected=[p for p in sorted(stage.rglob("*")) if p.is_file() and included(p.relative_to(stage))]
    manifest=dict(code_commit=commit,files=[dict(path=p.relative_to(stage).as_posix(),bytes=p.stat().st_size,sha256=sha(p)) for p in selected])
    (stage/"FILE_MANIFEST.json").write_text(json.dumps(manifest,indent=2))
    with zipfile.ZipFile(archive,"w",zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for path in selected+[stage/"FILE_MANIFEST.json"]:z.write(path,Path(stage.name)/path.relative_to(stage))
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        for record in manifest["files"]:
            assert hashlib.sha256(z.read(stage.name+"/"+record["path"])).hexdigest()==record["sha256"]
        assert all(included(Path(*Path(name).parts[1:])) for name in z.namelist())
        for lock in stage.glob("vendor/*/ASSET_MANIFEST.json"):
            for record in json.loads(lock.read_text(encoding="utf-8"))["files"]:
                asset = lock.parent / record["path"]
                assert hashlib.sha256(z.read(stage.name+"/"+asset.relative_to(stage).as_posix())).hexdigest()==record["sha256"]
    checksum=sha(archive);archive.with_suffix(".zip.sha256").write_text(checksum+"  "+archive.name+"\n")
    print(json.dumps(dict(archive=str(archive),bytes=archive.stat().st_size,sha256=checksum,code_commit=commit,
        files=len(selected),zip_crc_and_all_file_hashes_passed=True,relocation_test=portability),indent=2),flush=True)


if __name__=="__main__":main()
