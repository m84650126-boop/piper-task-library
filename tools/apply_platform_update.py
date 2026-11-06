"""Install only the reviewed sharing code; preserve user data/config and old code."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil

ROOT=Path(__file__).resolve().parent.parent


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--platform",type=Path,required=True);args=parser.parse_args()
    target=args.platform.resolve();source=ROOT/"platform_update"
    if not (target/"sim.py").is_file() or not (target/"workbench/core.py").is_file():
        raise ValueError("Target is not an existing PiPER collection platform")
    manifest=json.loads((ROOT/"platform_update_manifest.json").read_text(encoding="utf-8"))
    files=[]
    for name,checksum in manifest["files"].items():
        p=PurePosixPath(name)
        if p.is_absolute() or ".." in p.parts or "\\" in name or ":" in name:raise ValueError("Unsafe update path")
        src=source/name;dst=target/name
        if not dst.resolve().is_relative_to(target):raise ValueError("Update path escaped target")
        if hashlib.sha256(src.read_bytes()).hexdigest()!=checksum:raise ValueError("Update checksum mismatch: "+name)
        files.append((name,src,dst))
    backup=target/"data/platform_update_backups"/datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup.mkdir(parents=True)
    # Validate every input and back up every old file before changing the platform.
    for name,src,dst in files:
        if dst.exists():
            old=backup/name;old.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(dst,old)
    (backup/"update_manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    for name,src,dst in files:
        dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
    print(json.dumps(dict(status="installed",files=len(files),backup=str(backup),restart_required=True)))


if __name__=="__main__":main()
