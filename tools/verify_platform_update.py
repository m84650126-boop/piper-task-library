import hashlib
import json
from pathlib import Path

root=Path(__file__).resolve().parent.parent
manifest=json.loads((root/"platform_update_manifest.json").read_text(encoding="utf-8"))
for name,checksum in manifest["files"].items():
    if hashlib.sha256((root/"platform_update"/name).read_bytes()).hexdigest()!=checksum:
        raise ValueError("Platform update mismatch: "+name)
print("Platform update hashes passed")
