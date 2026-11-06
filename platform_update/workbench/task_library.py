"""Explicit Git library synchronization. Package installation stays revision-pinned."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from .task_packages import ROOT, digest, inspect_package, install_package, json_bytes, safe_name

DEFAULT_REPOSITORY = "https://github.com/m84650126-boop/piper-task-library.git"


def git(folder, *args):
    result = subprocess.run(["git", "-C", str(folder), *args], capture_output=True, timeout=120)
    if result.returncode:
        raise ValueError(result.stderr.decode("utf-8", errors="replace")[-1500:])
    return result.stdout


def refresh(repository=DEFAULT_REPOSITORY, root=None, branch="main"):
    root = Path(root or ROOT)
    if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", repository):
        raise ValueError("Library URL must be an HTTPS GitHub repository without embedded credentials")
    if not re.fullmatch(r"[A-Za-z0-9_/-]+", branch) or ".." in branch or branch.startswith("-"):
        raise ValueError("Invalid library branch")
    base = root / "task_library_cache"
    base.mkdir(parents=True, exist_ok=True)
    cache = base / (digest(repository.encode())[:16] + ".git")
    if not cache.exists():
        process = subprocess.run(["git", "clone", "--bare", "--branch", branch, repository, str(cache)], capture_output=True, timeout=120)
        if process.returncode:
            raise ValueError(process.stderr.decode("utf-8", errors="replace")[-1500:])
        revision = git(cache, "rev-parse", "HEAD").decode().strip()
    else:
        git(cache, "fetch", "origin", branch)
        revision = git(cache, "rev-parse", "FETCH_HEAD").decode().strip()
    catalog = read_catalog(cache, revision)
    snapshot = dict(repository=repository, branch=branch, revision=revision, cache=str(cache.resolve()))
    temporary=base / "active.tmp";temporary.write_bytes(json_bytes(snapshot));temporary.replace(base / "active.json")
    return {**snapshot, "task_versions": len(catalog["tasks"])}


def read_catalog(cache, revision):
    if not re.fullmatch(r"[a-f0-9]{40,64}", revision): raise ValueError("Invalid library revision")
    if int(git(cache,"cat-file","-s",f"{revision}:catalog.json"))>2*1024*1024:raise ValueError("Oversized library catalog")
    catalog = json.loads(git(cache, "show", f"{revision}:catalog.json"))
    if catalog.get("schema") != "piper_task_catalog_v1" or not isinstance(catalog.get("tasks"), list):
        raise ValueError("Unsupported library catalog")
    seen = set()
    for item in catalog["tasks"]:
        safe_name(item["archive"])
        pair = (item["id"], item["version"])
        if pair in seen or not re.fullmatch(r"[a-f0-9]{64}", item["archive_sha256"]):
            raise ValueError("Duplicate catalog task/version or invalid hash")
        seen.add(pair)
    return catalog


def active(root=None):
    root=Path(root or ROOT)
    snapshot=json.loads((root/"task_library_cache/active.json").read_text(encoding="utf-8"))
    cache=Path(snapshot["cache"]).resolve()
    if not cache.is_relative_to((root/"task_library_cache").resolve()):raise ValueError("Cache path escaped platform")
    return snapshot,read_catalog(cache,snapshot["revision"])


def install_from_library(task, version, root=None, enable=False):
    root=Path(root or ROOT)
    snapshot,catalog=active(root)
    selected=[e for e in catalog["tasks"] if e["id"]==task and e["version"]==version]
    if len(selected)!=1:raise ValueError("Task/version not found; run library list")
    entry=selected[0]
    if int(git(snapshot["cache"],"cat-file","-s",f"{snapshot['revision']}:{entry['archive']}"))>256*1024*1024:
        raise ValueError("Oversized library task package")
    content=git(snapshot["cache"],"show",f"{snapshot['revision']}:{entry['archive']}")
    if digest(content)!=entry["archive_sha256"]:raise ValueError("Library archive checksum mismatch")
    root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="library-install-",dir=root) as temp:
        archive=Path(temp)/"task.zip";archive.write_bytes(content)
        report=inspect_package(archive)
        if (report["manifest"]["id"],report["manifest"]["version"])!=(task,version):raise ValueError("Library package identity mismatch")
        if report["manifest_sha256"]!=entry["package_sha256"]:raise ValueError("Library package manifest mismatch")
        result=install_package(archive,root,enable)
    receipt=root/"task_library_cache"/"receipts"/(task+"_"+version+"_"+snapshot["revision"][:12]+".json")
    receipt.parent.mkdir(parents=True,exist_ok=True)
    receipt.write_bytes(json_bytes(dict(**snapshot,task=task,version=version,archive_sha256=entry["archive_sha256"],package_sha256=result["package_sha256"])))
    return {**result,"library_revision":snapshot["revision"],"receipt":str(receipt)}


def library_command(args):
    if args.action=="refresh":result=refresh(args.repository,branch=args.branch)
    elif args.action=="list":
        snapshot,catalog=active();result=dict(repository=snapshot["repository"],revision=snapshot["revision"],tasks=catalog["tasks"])
    else:
        if not args.task or not args.version:raise ValueError("install requires --task and --version")
        result=install_from_library(args.task,args.version,enable=args.enable)
    print(json.dumps(result,ensure_ascii=False,indent=2))
