"""Rebuild immutable task ZIPs and catalog without executing contributed tasks."""
import argparse
import json
from pathlib import Path
import tempfile
import sys

from task_packages import export_package, inspect_package, json_bytes, read_folder, seal_folder

ROOT = Path(__file__).resolve().parent.parent


def main():
    p = argparse.ArgumentParser(); p.add_argument("--check", action="store_true"); p.add_argument("--seal", type=Path)
    args = p.parse_args()
    if args.seal:
        if args.check: raise ValueError("--check cannot modify manifests")
        seal_folder(args.seal)
    entries = []
    for manifest in sorted((ROOT / "tasks").glob("*/*/task_package.json")):
        m, _, checksum = read_folder(manifest.parent)
        if manifest.parent.name != m["version"] or manifest.parent.parent.name != m["id"]:
            raise ValueError("Task directory id/version mismatch")
        relative = f"packages/{m['id']}/{m['version']}.zip"
        path = ROOT / relative
        with tempfile.TemporaryDirectory() as temp:
            result = export_package(manifest.parent, Path(temp) / "task.zip")
            content = Path(result["path"]).read_bytes()
            if path.exists() and path.read_bytes() != content:
                raise ValueError("Published package differs: increment version instead of replacing " + relative)
            if not path.exists():
                if args.check: raise ValueError("Missing package " + relative)
                path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content)
        validation = f"validation/{m['id']}/{m['version']}.json"
        validation_data = json.loads((ROOT / validation).read_text(encoding="utf-8")) if (ROOT / validation).exists() else {"status": "unvalidated"}
        entries.append(dict(id=m["id"], version=m["version"], description=m["description"],
            archive=relative, archive_sha256=result["sha256"], package_sha256=checksum,
            validation=validation_data, validation_record=validation if (ROOT / validation).exists() else None))
    catalog = json_bytes(dict(schema="piper_task_catalog_v1", tasks=entries))
    if args.check:
        if (ROOT / "catalog.json").read_bytes() != catalog: raise ValueError("Catalog is stale")
    else: (ROOT / "catalog.json").write_bytes(catalog)
    print(json.dumps(dict(status="passed", task_versions=len(entries), check_only=args.check)))


if __name__ == "__main__": main()
