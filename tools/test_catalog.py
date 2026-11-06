"""A changed ZIP encoding is portable; changed task payload requires a new version."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT=Path(__file__).resolve().parent.parent


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        for folder in ("tasks","packages","validation","tools"):
            shutil.copytree(ROOT/folder,self.root/folder,ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(ROOT/"catalog.json",self.root/"catalog.json")

    def tearDown(self):self.temp.cleanup()

    def run_check(self):
        return subprocess.run([sys.executable,str(self.root/"tools/build_catalog.py"),"--check"],capture_output=True,text=True)

    def test_other_zip_platform_encoding_with_same_payload_passes(self):
        path=self.root/"packages/shared_reach/1.0.0.zip"
        with zipfile.ZipFile(path) as src:
            entries=[(info.filename,src.read(info)) for info in src.infolist()]
        original=path.read_bytes()
        with zipfile.ZipFile(path,"w",zipfile.ZIP_STORED) as dst:
            for name,data in reversed(entries):
                info=zipfile.ZipInfo(name,(2026,1,1,0,0,0));info.create_system=3;dst.writestr(info,data)
        self.assertNotEqual(original,path.read_bytes())
        catalog=json.loads((self.root/"catalog.json").read_text(encoding="utf-8"))
        catalog["tasks"][0]["archive_sha256"]=hashlib.sha256(path.read_bytes()).hexdigest()
        from task_packages import json_bytes
        (self.root/"catalog.json").write_bytes(json_bytes(catalog))
        result=self.run_check();self.assertEqual(result.returncode,0,result.stderr)

    def test_changed_source_under_same_version_fails(self):
        folder=self.root/"tasks/shared_reach/1.0.0"
        with (folder/"README.md").open("a",encoding="utf-8") as out:out.write("\nChanged task content\n")
        from task_packages import seal_folder
        seal_folder(folder)
        result=self.run_check()
        self.assertNotEqual(result.returncode,0)
        self.assertIn("increment version",result.stderr)


if __name__=="__main__":unittest.main()
