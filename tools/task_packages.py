"""Portable task bundles. Inspect/install never execute package Python code."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = "piper_task_package_v1"
MAX_BYTES = 256 * 1024 * 1024
MAX_FILES = 4096
BUILTINS = {"pick_place", "push", "pull", "wipe"}
SUFFIXES = {".py", ".json", ".xml", ".stl", ".obj", ".mtl", ".png", ".jpg", ".jpeg",
            ".txt", ".md", ".npy", ".npz", ".dae", ".ply", ".yaml", ".yml", ".toml", ".webp", ".msh"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def safe_name(name):
    p = PurePosixPath(name)
    if not name or any(c in name for c in '\\:<>|?*') or any(ord(c)<32 for c in name) or p.is_absolute() or any(x in ("", ".", "..") for x in name.split("/")):
        raise ValueError("Unsafe package path: " + name)
    for part in p.parts:
        if part.endswith((" ", ".")) or part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
            raise ValueError("Nonportable package path: " + name)
    return p


def manifest_check(m):
    if m.get("schema") != SCHEMA or m.get("api_version") != 1:
        raise ValueError("Unsupported task package schema/API")
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", m.get("id", "")) or m["id"] in BUILTINS:
        raise ValueError("Invalid/reserved task id")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9.-]+)?", m.get("version", "")):
        raise ValueError("Task package requires a semantic version")
    if m.get("robot") != "piper_l" or not m.get("description") or not m.get("author"):
        raise ValueError("Package requires robot=piper_l, author and description")
    files = m.get("files")
    if not isinstance(files, dict) or not 1 <= len(files) <= MAX_FILES:
        raise ValueError("Invalid file inventory")
    folded = set()
    for name, checksum in files.items():
        p = safe_name(name)
        if name == "task_package.json" or p.suffix.lower() not in SUFFIXES or name.casefold() in folded:
            raise ValueError("Unsupported/duplicate payload path: " + name)
        if any(x.startswith(".") or x in ("__pycache__", "data", "pretrained_model") for x in p.parts):
            raise ValueError("Private/generated payload directory: " + name)
        if not re.fullmatch(r"[a-f0-9]{64}", checksum):
            raise ValueError("Invalid file checksum")
        folded.add(name.casefold())
    for key in ("entrypoint", "license_file", "scene_config"):
        if m.get(key) not in files:
            raise ValueError("Missing package file: " + key)
    if not m["entrypoint"].endswith(".py"):
        raise ValueError("Entrypoint must be Python")
    return m


def _verify(m, payload):
    manifest_check(m)
    if set(payload) != set(m["files"]):
        raise ValueError("Unlisted or missing package files")
    if sum(len(x) for x in payload.values()) > MAX_BYTES:
        raise ValueError("Task package exceeds 256 MiB")
    for name, content in payload.items():
        if digest(content) != m["files"][name]:
            raise ValueError("Package file checksum mismatch: " + name)
    scene = json.loads(payload[m["scene_config"]])
    if not isinstance(scene, dict):
        raise ValueError("Scene config must be a JSON object")
    if scene.get("task", m["id"]) != m["id"]:
        raise ValueError("Scene task id mismatch")
    return scene


def read_folder(folder):
    folder = Path(folder).resolve()
    manifest_path = folder / "task_package.json"
    m = manifest_check(json.loads(manifest_path.read_text(encoding="utf-8")))
    payload = {}
    for name in m["files"]:
        path = folder.joinpath(*safe_name(name).parts)
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(folder):
            raise ValueError("Missing or external/symlink package file: " + name)
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("Oversized package file")
        payload[name] = path.read_bytes()
    actual = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    if actual != set(m["files"]) | {"task_package.json"}:
        raise ValueError("Folder has unlisted files; update manifest first")
    _verify(m, payload)
    return m, payload, digest(manifest_path.read_bytes())


def read_archive(archive):
    with zipfile.ZipFile(archive) as z:
        infos = z.infolist()
        if not 2 <= len(infos) <= MAX_FILES + 1 or sum(i.file_size for i in infos) > MAX_BYTES:
            raise ValueError("Oversized/empty archive")
        seen = set()
        for i in infos:
            safe_name(i.filename)
            if i.filename.casefold() in seen or i.is_dir() or stat.S_ISLNK(i.external_attr >> 16):
                raise ValueError("Duplicate, directory or symlink ZIP entry")
            seen.add(i.filename.casefold())
        manifest = z.read("task_package.json")
        m = manifest_check(json.loads(manifest))
        payload = {i.filename: z.read(i) for i in infos if i.filename != "task_package.json"}
    scene = _verify(m, payload)
    return m, payload, digest(manifest), scene


def seal_folder(folder):
    """Explicit author action to update the file inventory, without importing code."""
    folder = Path(folder).resolve()
    manifest_path = folder / "task_package.json"
    m = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = {}
    for path in sorted(folder.rglob("*")):
        if "__pycache__" in path.parts:
            continue
        if path.is_symlink():
            raise ValueError("Symlinks are not portable")
        if not path.is_file() or path == manifest_path:
            continue
        name = path.relative_to(folder).as_posix()
        files[name] = digest(path.read_bytes())
    m["files"] = files
    manifest_check(m)
    manifest_path.write_bytes(json_bytes(m))
    read_folder(folder)
    return m


def export_package(folder, output):
    m, payload, _ = read_folder(folder)
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Output exists; choose a new path")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", zipfile.ZIP_DEFLATED) as z:
        for name, content in sorted({**payload, "task_package.json": json_bytes(m)}.items()):
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, content)
    read_archive(output)
    return dict(path=str(output), sha256=digest(output.read_bytes()), id=m["id"], version=m["version"])


def inspect_package(archive):
    m, _, checksum, scene = read_archive(archive)
    return dict(manifest=m, manifest_sha256=checksum, archive_sha256=digest(Path(archive).read_bytes()), scene=scene,
                code_executed=False)


def resolve_asset_references(xml, package_root):
    """Resolve portable task-package:// paths only for model compilation.

    Keep the unexpanded XML in env.source_xml for portable recorded replay.
    """
    root=ET.fromstring(xml);folder=Path(package_root).resolve()
    for element in root.iter():
        value=element.get("file", "")
        if value.startswith("task-package://"):
            name=value.removeprefix("task-package://");safe_name(name)
            path=(folder/name).resolve()
            if not path.is_relative_to(folder) or not path.is_file():raise ValueError("Missing package asset: "+name)
            element.set("file",str(path))
    return ET.tostring(root,encoding="unicode")


def install_package(archive, root=None, enable=False):
    """Validate, retain immutable version, optionally configure future code loading."""
    root = Path(root or ROOT).resolve()
    m, payload, checksum, scene = read_archive(archive)
    directory = root / "task_plugins"
    index = directory / "enabled.json"
    data = json.loads(index.read_text(encoding="utf-8")) if index.exists() else dict(api_version=1, plugins=[])
    if data.get("api_version") != 1:
        raise ValueError("Unsupported plugin index")
    if enable and any(e["id"] == m["id"] and e.get("package_sha256") != checksum for e in data["plugins"]):
        raise ValueError("Another version is enabled; explicitly disable it first. Old files are retained.")
    versions = root / "task_packages" / m["id"]
    versions.mkdir(parents=True, exist_ok=True)
    siblings = list(versions.glob(m["version"] + "-*"))
    target = versions / (m["version"] + "-" + checksum[:16])
    if siblings and target not in siblings:
        raise ValueError("Same id/version has different content; increment task version")
    if target.exists():
        if read_folder(target)[2] != checksum:
            raise ValueError("Installed version has changed")
    else:
        tmp = Path(tempfile.mkdtemp(prefix="incoming-", dir=versions))
        try:
            for name, content in {**payload, "task_package.json": json_bytes(m)}.items():
                path = tmp.joinpath(*safe_name(name).parts)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            # Canonical manifests are required so installed provenance matches archive.
            if read_folder(tmp)[2] != checksum:
                raise ValueError("Noncanonical manifest; export with the platform tool")
            tmp.rename(target)
        finally:
            if tmp.exists():
                shutil.rmtree(tmp)
    if enable:
        directory.mkdir(exist_ok=True)
        entry = dict(id=m["id"], version=m["version"],
                     file=(target / m["entrypoint"]).relative_to(root).as_posix(),
                     sha256=m["files"][m["entrypoint"]], package_sha256=checksum,
                     package_manifest=(target / "task_package.json").relative_to(root).as_posix())
        data["plugins"] = [e for e in data["plugins"] if e["id"] != m["id"]] + [entry]
        tmp_index = index.with_suffix(".tmp")
        tmp_index.write_bytes(json_bytes(data)); tmp_index.replace(index)
    return dict(id=m["id"], version=m["version"], path=str(target), enabled=enable,
                package_sha256=checksum, scene=scene, restart_required=enable, code_executed=False)


def package_command(args):
    if args.action == "seal":
        result = seal_folder(args.source)
    elif args.action == "export":
        if not args.output: raise ValueError("export requires --output")
        result = export_package(args.source, args.output)
    elif args.action == "inspect":
        result = inspect_package(args.source)
    else:
        result = install_package(args.source, enable=args.enable)
    print(json.dumps(result, ensure_ascii=False, indent=2))
