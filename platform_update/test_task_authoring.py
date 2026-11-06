"""Created tasks keep real base physics, evidence, portable sources and honest draft status."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from workbench.core import ROOT


class TaskAuthoring(unittest.TestCase):
    def test_custom_draft_cannot_be_enabled_and_does_not_claim_validation(self):
        from workbench.task_authoring import create_task
        with tempfile.TemporaryDirectory(dir=ROOT/"data") as folder:
            spec=dict(id="author_custom",label="自定义待开发任务",author="QA",base_task="custom")
            with self.assertRaises(ValueError):create_task(spec,folder,enable=True)
            result=create_task(spec,folder)
            self.assertFalse(result["enabled"])
            self.assertEqual(result["validation_status"],"parameters_only_not_physically_validated")
            self.assertIn("NotImplementedError",(Path(result["source"])/"task.py").read_text(encoding="utf-8"))

    def test_invalid_conditions_and_existing_ids_do_not_create_task(self):
        from workbench.task_authoring import create_task
        with tempfile.TemporaryDirectory(dir=ROOT/"data") as folder:
            for spec in (dict(id="push",base_task="push"),dict(id="author_bad",base_task="push",preset=dict(goal_xy=[.26,-.1]))):
                with self.assertRaises(ValueError):create_task(dict(label="QA",author="QA",**spec),folder)
            self.assertFalse((Path(folder)/"task_sources").exists())

    def test_all_four_created_variants_collect_and_physically_replay(self):
        from workbench.task_authoring import create_task
        from workbench import plugin_api
        from workbench.tasks import TASKS
        from workbench.core import validate_recipe,make_environment,make_writer,preflight,expert
        from validate import validate_episode
        saved_tasks=dict(TASKS);saved_plugins=dict(plugin_api.PLUGINS)
        try:
            with tempfile.TemporaryDirectory(prefix="created task ",dir=ROOT/"data") as folder:
                for base in ("pick_place","push","pull","wipe"):
                    task="author_test_"+base
                    result=create_task(dict(id=task,label="QA · "+base,author="QA",base_task=base,
                        preset=dict(object="red_cube" if base=="wipe" else "cylinder")),folder,enable=True)
                # Load once after all sources are installed, as a real restart does.
                with patch.object(plugin_api,"ROOT",Path(folder)):plugin_api.load_enabled({})
                for task,plugin in plugin_api.PLUGINS.items():
                    if task.startswith("author_test_"):
                        TASKS[task]=dict(label=plugin.label,text=plugin.text,hint=plugin.hint,preset=plugin.preset,phases=plugin.phases)
                for base in ("pick_place","push","pull","wipe"):
                    with self.subTest(base=base):
                        task="author_test_"+base;plugin=plugin_api.get_plugin(task)
                        recipe=validate_recipe({**plugin.preset,"task":task,"mode":"fixed"})
                        env=make_environment(True,task,recipe=recipe)
                        try:
                            env.reset_scene(recipe["cube_xy"],recipe["goal_xy"],860001)
                            writer=make_writer(Path(folder)/base,env,"scripted",recipe)
                            for action,stage in expert(env,preflight(env)):
                                writer.append(env.observe(),action,stage);env.step(action)
                            path=writer.finish(env,float(env.cube_position()[2]))
                            self.assertTrue(writer.report["success"],writer.report)
                            checked=validate_episode(path)
                            self.assertTrue(checked["replay"][0]["passed"])
                            self.assertEqual(recipe["task_plugin"]["base_task"],base)
                        finally:env.close()
        finally:
            TASKS.clear();TASKS.update(saved_tasks);plugin_api.PLUGINS.clear();plugin_api.PLUGINS.update(saved_plugins)


if __name__=="__main__":unittest.main()
