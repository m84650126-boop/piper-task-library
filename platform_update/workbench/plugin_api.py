"""Versioned local task extensions. Only explicitly enabled, hash-pinned code loads."""
from dataclasses import dataclass,field
from pathlib import Path
from typing import Callable
import hashlib
import importlib.util
import json
import re
import sys

API_VERSION=1
ROOT=Path(__file__).resolve().parent.parent
PLUGINS={}


@dataclass
class TaskPlugin:
    id: str
    version: str
    label: str
    text: str
    hint: str
    phases: list
    make_environment: Callable
    check_condition: Callable
    preflight: Callable
    expert: Callable
    evaluate: Callable
    preset: dict=field(default_factory=dict)
    evidence_keys: tuple=()
    prepare_recording: Callable|None=None
    restore_initial: Callable|None=None
    source_sha256: str=""
    source_file: str=""
    package_sha256: str=""
    package_directory: str=""
    base_task: str=""

    def metadata(self):
        result=dict(api_version=API_VERSION,id=self.id,version=self.version,
                    source_sha256=self.source_sha256,source_file=self.source_file)
        if self.package_sha256:result["package_sha256"]=self.package_sha256
        if self.base_task:result["base_task"]=self.base_task
        return result


def read_plugin(path,package_manifest=None,expected_package_sha256=None):
    path=Path(path).resolve()
    package=None
    if package_manifest:
        from .task_packages import read_folder
        manifest_path=Path(package_manifest).resolve()
        package,_,checksum=read_folder(manifest_path.parent)
        if checksum!=expected_package_sha256:raise ValueError("Task package manifest changed")
        if path!=(manifest_path.parent/package["entrypoint"]).resolve():raise ValueError("Wrong package entrypoint")
    spec=importlib.util.spec_from_file_location("piper_task_"+hashlib.sha256(str(path).encode()).hexdigest()[:12],path,
        submodule_search_locations=[str(path.parent)] if package else None)
    if not spec or not spec.loader:raise ValueError("Cannot import task plugin")
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    if getattr(module,"API_VERSION",None)!=API_VERSION:raise ValueError("Unsupported task API version")
    plugin=module.get_task()
    if not isinstance(plugin,TaskPlugin) or not re.fullmatch(r"[a-z][a-z0-9_]{2,63}",plugin.id):
        raise ValueError("Invalid task plugin definition/id")
    if len(plugin.phases)!=3 or len({p[0] for p in plugin.phases})!=3:raise ValueError("APIv1 requires3 unique phase buttons")
    for hook in ("make_environment","check_condition","preflight","expert","evaluate"):
        if not callable(getattr(plugin,hook)):raise ValueError("Missing plugin hook: "+hook)
    if not plugin.version or not plugin.label or not plugin.text:raise ValueError("Task version/label/text required")
    reserved={"state","cube_xyz","tcp_xyz","timestamp","qpos","qvel","integration_state","overview","wrist",
        "finger_contacts","table_contact","held_over_interval","cube_velocity","cube_in_tcp","cube_rotation_in_tcp"}
    if any(not isinstance(key,str) or not re.fullmatch(r"[a-z][a-z0-9_]*",key) or key in reserved for key in plugin.evidence_keys):
        raise ValueError("Plugin evidence keys must be unique diagnostic names, not core observation fields")
    if len(set(plugin.evidence_keys))!=len(plugin.evidence_keys):raise ValueError("Duplicate plugin evidence key")
    plugin.source_sha256=hashlib.sha256(path.read_bytes()).hexdigest();plugin.source_file=path.name
    if package:
        if plugin.id!=package["id"] or plugin.version!=package["version"]:raise ValueError("Code and task package id/version mismatch")
        plugin.package_sha256=checksum
        plugin.package_directory=str(manifest_path.parent)
    return plugin


def load_enabled(catalog):
    directory=ROOT/"task_plugins";index=directory/"enabled.json"
    if not index.exists():return
    data=json.loads(index.read_text(encoding="utf-8"))
    if data.get("api_version")!=API_VERSION:raise ValueError("Unsupported plugin index")
    for entry in data["plugins"]:
        packaged=bool(entry.get("package_manifest"))
        path=((ROOT if packaged else directory)/entry["file"]).resolve()
        if path.suffix!=".py":raise ValueError("Plugin entrypoint must be Python")
        manifest=None
        if packaged:
            manifest=(ROOT/entry["package_manifest"]).resolve()
            if not manifest.is_relative_to((ROOT/"task_packages").resolve()) or not path.is_relative_to(manifest.parent):
                raise ValueError("Package path escaped its directory")
        elif path.parent!=directory.resolve():raise ValueError("Plugin path escaped its directory")
        if hashlib.sha256(path.read_bytes()).hexdigest()!=entry["sha256"]:
            raise ValueError("Enabled task code changed; disable/review/re-enable it: "+path.name)
        plugin=read_plugin(path,manifest,entry.get("package_sha256"))
        if plugin.id!=entry["id"] or plugin.id in catalog:raise ValueError("Duplicate/reserved task id: "+plugin.id)
        if any(v["label"]==plugin.label for v in catalog.values()):raise ValueError("Duplicate task label")
        PLUGINS[plugin.id]=plugin
        catalog[plugin.id]=dict(label=plugin.label,text=plugin.text,hint=plugin.hint,phases=plugin.phases,
            preset=plugin.preset,plugin=plugin.metadata())


def get_plugin(task):return PLUGINS.get(task)


def physical_task(task):
    plugin=get_plugin(task)
    return plugin.base_task if plugin and plugin.base_task else task


def plugin_metadata(task):
    plugin=get_plugin(task)
    if plugin and plugin.package_sha256:
        from .task_packages import read_folder
        if read_folder(plugin.package_directory)[2]!=plugin.package_sha256:raise ValueError("Task package changed")
    return plugin.metadata() if plugin else None
