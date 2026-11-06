"""Create editable task sources and a sealed portable bundle, without editing XML by hand."""
import json
from pathlib import Path
import re
from .task_packages import ROOT,json_bytes,seal_folder,export_package,install_package

BUILTINS=("pick_place","push","pull","wipe")


def create_task(spec,root=None,enable=False):
    from .core import DEFAULT,validate_recipe,sample_condition
    from .tasks import TASKS
    root=Path(root or ROOT).resolve();task=spec.get("id","").strip();base=spec.get("base_task","push")
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,63}",task) or task in TASKS:raise ValueError("任务ID需为3~64位小写字母/数字/下划线，首位为字母，且不能与现有任务重复")
    label=spec.get("label","").strip();author=spec.get("author","").strip()
    if not label or not author:raise ValueError("请填写任务名称和作者")
    if any(item["label"]==label for item in TASKS.values()):raise ValueError("任务名称已存在")
    if base not in BUILTINS+("custom",):raise ValueError("未知任务模板")
    if base=="custom" and enable:raise ValueError("全新动作草稿需要先实现代码，不能直接启用")
    recipe={**DEFAULT,**TASKS[base if base in BUILTINS else "pick_place"].get("preset",{}),**spec.get("preset",{})}
    recipe["task"]=base if base in BUILTINS else "pick_place"
    recipe=validate_recipe(recipe)
    sample_condition({**recipe,"mode":"fixed"},int(recipe["seed"]))
    sample_condition({**recipe,"mode":"random"},int(recipe["seed"]))
    preset={key:value for key,value in recipe.items() if key not in ("task","task_plugin")}
    config=dict(id=task,version="1.0.0",label=label,base_task=base,
        text=spec.get("text","").strip() or (TASKS[base]["text"] if base in BUILTINS else "Describe the new task here."),preset=preset)
    folder=root/"task_sources"/task/"1.0.0"
    if folder.exists():raise ValueError("任务源目录已存在；请使用新的ID或在已有源码中升级版本")
    folder.mkdir(parents=True)
    if base in BUILTINS:
        code='''"""Generated configuration variant. Edit scene.json; then seal and export a NEW version."""
from pathlib import Path
import json
from workbench.task_templates import build_variant
API_VERSION=1
def get_task():
    return build_variant(json.loads(Path(__file__).with_name("scene.json").read_text(encoding="utf-8")))
'''
    else:
        code='''"""DEVELOPMENT DRAFT: implement every hook before enabling this task."""
from pathlib import Path
import json
from workbench.plugin_api import TaskPlugin
API_VERSION=1
def pending(*args,**kwargs):
    raise NotImplementedError("Implement scene, expert and success criteria before collecting this task")
def get_task():
    c=json.loads(Path(__file__).with_name("scene.json").read_text(encoding="utf-8"))
    return TaskPlugin(id=c["id"],version=c["version"],label=c["label"],text=c["text"],hint="未实现的开发草稿，不能采集",
        phases=[("approach","1 准备"),("execute","2 执行"),("settle","3 完成")],preset=c["preset"],
        make_environment=pending,check_condition=pending,preflight=pending,expert=pending,evaluate=pending)
'''
    (folder/"task.py").write_text(code,encoding="utf-8")
    (folder/"scene.json").write_bytes(json_bytes(config))
    (folder/"LICENSE.txt").write_text("Project-wide license not yet selected. Confirm distribution terms with the task author.\n",encoding="utf-8")
    (folder/"README.md").write_text(f"# {label}\n\n任务ID：{task}；作者：{author}；版本：1.0.0\n\n"+
        (f"这是 {base} 的场景/物品/参数配置变体，沿用该任务的控制器、阶段和成功判据。\n创建时只检查了参数范围，尚未完成专家试跑、物理回放、转换或策略验证。\n" if base in BUILTINS else "这是待开发草稿。请实现task.py中的环境、启动条件、专家轨迹和纯数据成功判据。\n不要将未实现的草稿用于采集。\n")+
        "\nscene.json保存默认场景、物品、坐标和随机范围。新增资产需放在本包中并记录来源与许可。\n修改后升级scene.json与task_package.json中的版本，再seal/export。\n",encoding="utf-8")
    (folder/"task_package.json").write_bytes(json_bytes(dict(schema="piper_task_package_v1",api_version=1,id=task,version="1.0.0",
        robot="piper_l",author=author,description=label,entrypoint="task.py",scene_config="scene.json",license_file="LICENSE.txt",
        creation_mode="configuration_variant" if base in BUILTINS else "developer_draft",base_task=base,validation_status="parameters_only_not_physically_validated",files={})))
    seal_folder(folder)
    archive=folder.parent/(task+"_1.0.0.zip");package=export_package(folder,archive)
    installed=install_package(archive,root,enable=True) if enable else None
    return dict(id=task,source=str(folder),archive=str(archive),sha256=package["sha256"],enabled=enable,installed=installed,
        restart_required=enable,base_task=base,validation_status="parameters_only_not_physically_validated")
