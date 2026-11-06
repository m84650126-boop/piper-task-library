"""Task sharing: relocation, model assets, provenance and hostile/broken archives."""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from workbench import task_packages as packages

ROOT = packages.ROOT


class TaskPackages(unittest.TestCase):
    def setUp(self):
        (ROOT / "data").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="task package test ", dir=ROOT / "data")
        self.folder = Path(self.temp.name)
        self.source = self.folder / "author source"
        shutil.copytree(ROOT / "examples/shared_reach", self.source, ignore=shutil.ignore_patterns("__pycache__"))
        self.archive = self.folder / "task.zip"
        packages.export_package(self.source, self.archive)

    def tearDown(self):
        self.temp.cleanup()

    def test_export_is_deterministic_and_import_never_executes(self):
        second = self.folder / "second.zip"
        packages.export_package(self.source, second)
        self.assertEqual(self.archive.read_bytes(), second.read_bytes())
        self.assertFalse(packages.inspect_package(self.archive)["code_executed"])
        install = packages.install_package(self.archive, self.folder / "other computer", enable=True)
        self.assertFalse(install["code_executed"])
        self.assertTrue((Path(install["path"]) / "assets/marker.png").exists())

    @unittest.skipUnless(__import__("os").name=="nt", "Windows directory ACL regression")
    def test_installed_directory_inherits_destination_acl(self):
        import os
        import subprocess
        result=packages.install_package(self.archive,self.folder/"receiver")
        command=" $ErrorActionPreference='Stop'; (Get-Acl -LiteralPath $env:PIPER_ACL_TEST_TARGET).AreAccessRulesProtected"
        checked=subprocess.run(["powershell.exe","-NoProfile","-NonInteractive","-Command",command],
            env={**os.environ,"PIPER_ACL_TEST_TARGET":result["path"]},capture_output=True,text=True,check=True)
        self.assertEqual(checked.stdout.strip(),"False","Installed tasks must inherit the destination ACL, not mkdtemp's owner-only ACL")

    def test_tampered_assets_and_extra_payload_rejected(self):
        for extra in (False, True):
            target = self.folder / ("extra.zip" if extra else "tampered.zip")
            with zipfile.ZipFile(self.archive) as src, zipfile.ZipFile(target, "w") as dst:
                for info in src.infolist():
                    content = src.read(info)
                    if not extra and info.filename == "assets/marker.png": content += b"modified"
                    dst.writestr(info.filename, content)
                if extra: dst.writestr("surprise.py", "raise RuntimeError('must not execute')")
            with self.assertRaises(ValueError): packages.install_package(target, self.folder / "receiver")

    def test_zip_traversal_duplicates_and_reserved_paths_rejected(self):
        for i, name in enumerate(("../outside.py", "C:/outside.py", "assets/CON.txt", "assets/a:b.py")):
            bad = self.folder / f"bad{i}.zip"
            with zipfile.ZipFile(bad, "w") as z:
                z.writestr("task_package.json", "{}")
                z.writestr(name, "bad")
            with self.assertRaises(ValueError): packages.inspect_package(bad)
        self.assertFalse((self.folder / "outside.py").exists())

    def test_changed_same_version_rejected_and_old_version_retained(self):
        receiver = self.folder / "receiver"
        old = packages.install_package(self.archive, receiver, enable=True)
        (self.source / "scene.json").write_text(json.dumps({"task": "shared_reach", "target_height_m": .19}), encoding="utf-8")
        packages.seal_folder(self.source)
        changed = self.folder / "changed.zip"; packages.export_package(self.source, changed)
        with self.assertRaises(ValueError): packages.install_package(changed, receiver)
        self.assertEqual(packages.read_folder(old["path"])[0]["version"], "1.0.0")

    def test_asset_changes_rejected_before_plugin_import(self):
        from workbench.plugin_api import read_plugin
        result = packages.install_package(self.archive, self.folder / "receiver")
        folder = Path(result["path"])
        (folder / "assets/marker.xml").write_text("<mujoco/>", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "checksum"):
            read_plugin(folder / "task.py", folder / "task_package.json", result["package_sha256"])

    def test_relocated_task_collects_rgb_and_replays_using_packaged_texture(self):
        from workbench import plugin_api
        from workbench.core import validate_recipe, make_environment, make_writer, preflight, expert
        from workbench.tasks import TASKS
        from validate import validate_episode
        import h5py
        receiver = self.folder / "receiver with spaces"
        previous_task=TASKS.pop("shared_reach",None)
        previous_plugin=plugin_api.PLUGINS.pop("shared_reach",None)
        result = packages.install_package(self.archive, receiver, enable=True)
        # The receiver has no dependency on the author's source directory.
        shutil.rmtree(self.source)
        try:
            with patch.object(plugin_api, "ROOT", receiver): plugin_api.load_enabled(TASKS)
            recipe = validate_recipe({"task": "shared_reach", "environment": "robosuite_lab"})
            self.assertEqual(recipe["task_plugin"]["package_sha256"], result["package_sha256"])
            env = make_environment(True, "shared_reach", recipe=recipe)
            try:
                env.reset_scene([.40, -.10], [.34, .18], 80001)
                self.assertGreaterEqual(env.model.geom("shared_scene_marker").id, 0)
                writer = make_writer(self.folder, env, "scripted", recipe)
                for action, stage in expert(env, preflight(env)):
                    writer.append(env.observe(), action, stage); env.step(action)
                path = writer.finish(env, .02)
                self.assertTrue(writer.report["success"])
            finally: env.close()
            with h5py.File(path) as f:
                self.assertIn("task-package://assets/marker.png", f.attrs["scene_xml"])
                self.assertEqual(json.loads(f.attrs["task_plugin_json"])["package_sha256"], result["package_sha256"])
            self.assertTrue(validate_episode(path)["replay"][0]["passed"])
        finally:
            TASKS.pop("shared_reach", None); plugin_api.PLUGINS.pop("shared_reach", None)
            if previous_task:TASKS["shared_reach"]=previous_task
            if previous_plugin:plugin_api.PLUGINS["shared_reach"]=previous_plugin


class TaskLibrary(unittest.TestCase):
    def test_git_snapshot_install_and_corruption_detection(self):
        import subprocess
        from workbench.task_library import install_from_library
        with tempfile.TemporaryDirectory(dir=ROOT/"data") as tmp:
            root=Path(tmp);cache=root/"task_library_cache/local";cache.mkdir(parents=True)
            def git(*args):
                return subprocess.check_output(["git","-c","core.autocrlf=false","-c","commit.gpgsign=false","-c","user.name=Task QA","-c","user.email=qa@local.invalid","-C",str(cache),*args],stderr=subprocess.DEVNULL).decode().strip()
            git("init","-b","main")
            archive=cache/"packages/shared_reach.zip";result=packages.export_package(ROOT/"examples/shared_reach",archive)
            report=packages.inspect_package(archive)
            catalog=dict(schema="piper_task_catalog_v1",tasks=[dict(id="shared_reach",version="1.0.0",archive="packages/shared_reach.zip",archive_sha256=result["sha256"],package_sha256=report["manifest_sha256"])])
            (cache/"catalog.json").write_bytes(packages.json_bytes(catalog))
            git("add",".");git("commit","-m","known task")
            revision=git("rev-parse","HEAD")
            snapshot=dict(repository="local QA",revision=revision,cache=str(cache))
            state=root/"task_library_cache/active.json";state.write_bytes(packages.json_bytes(snapshot))
            # A later branch commit does not change the previously selected snapshot.
            archive.write_bytes(b"corrupt");git("add",".");git("commit","-m","bad update")
            install=install_from_library("shared_reach","1.0.0",root)
            self.assertEqual(install["library_revision"],revision)
            self.assertTrue(Path(install["receipt"]).is_file())
            state.write_bytes(packages.json_bytes({**snapshot,"revision":git("rev-parse","HEAD")}))
            with self.assertRaisesRegex(ValueError,"checksum"):
                install_from_library("shared_reach","1.0.0",root)


if __name__ == "__main__": unittest.main()
