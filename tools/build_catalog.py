"""Rebuild immutable task ZIPs and catalog without executing contributed tasks."""
import argparse
import json
from pathlib import Path

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
        if path.exists():
            # ZIP headers and zlib output can vary across OS/Python versions.
            # Preserve the published bytes and verify their full payload against source.
            report = inspect_package(path)
            if report["manifest"] != m or report["manifest_sha256"] != checksum:
                raise ValueError("Published package differs: increment version instead of replacing " + relative)
            result = dict(sha256=report["archive_sha256"])
        else:
            if args.check: raise ValueError("Missing package " + relative)
            result = export_package(manifest.parent, path)
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
