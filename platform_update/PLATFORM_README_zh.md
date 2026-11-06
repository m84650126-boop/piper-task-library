# PiPER Data Workbench · 可迁移技术预览版

每个人在自己的电脑上安装运行，共用相同的数据协议和任务扩展接口。默认离线保存数据；不需要Codex、账户或在线服务。
当前机器人适配器是PiPER-L；任务APIv1不是任意机器人接口，也不是多人网页服务器。

## 安装与启动

先将完整发布包解压到可写目录，路径可含空格。不要复制别人电脑上的.venv。
采集环境要求Python3.12或3.13；转换单独使用Python3.12。采集不要求CUDA。

Windows：

```powershell
python setup_platform.py
.\.venv\Scripts\python.exe platform_cli.py doctor --physics --render
.\start_workbench.cmd
```

Linux（安装/启动入口已提供，尚未在本项目Linux实机验证）：

```bash
python3.12 setup_platform.py
.venv/bin/python platform_cli.py doctor --physics --render
bash start_workbench.sh
```

Linux桌面需要对应Python的Tk绑定、OpenGL/GLFW和可用显示服务。无显示服务器仅做脚本采集时，可配置本机支持的MuJoCo离屏后端，再运行collect；不把没有图形驱动当作模型问题。
Windows11 + Python3.13为当前实测组合。doctor会报告实际系统、依赖、资源校验、物理/图像检查和任务列表。

可选数据转换环境（先安装Python3.12）：

```powershell
py -3.12 setup_platform.py --conversion
```

Linux使用 `python3.12 setup_platform.py --conversion`。固定LeRobot0.6.2源码commit与Dataset v3.0；这里安装CPU转换环境，GPU训练另按训练配置建立环境。

## 使用

界面支持手动摆放、键盘录制、动作专家单条执行、独立随机区域批采、预览/回收站、物理检查和LeRobot导出。
默认数据目录为解压目录下data。可设置环境变量PIPER_WORKBENCH_DATA迁移数据盘；PIPER_CONVERSION_PYTHON可指定其他转换Python，PIPER_HF_CACHE可指定短缓存路径。
所有模型和纹理已经包含；正常运行不需要原作者的D盘URDF仓库，也不需要重新生成XML。

无界面脚本采集：

```powershell
.\.venv\Scripts\python.exe platform_cli.py collect --task reach_target --episodes 3 --random
```

`--diagnostics-only`只采物理诊断、不录RGB，不能直接作为训练数据。
完整操作见WORKBENCH_zh.md，场景与模型范围见SCENE_ASSETS_zh.md。

## 任务扩展

任务包导出/导入及共享 Git 任务库见 TASK_SHARING_zh.md。工作台顶部“任务共享”菜单支持本地任务 ZIP。

内置拾放、推、拉、擦拭；预览版额外启用单文件示例reach_target，证明独立扩展链路。
开发者复制examples/reach_target.py，修改任务id、版本、场景/专家及成功判据，再明确启用：

```powershell
.\.venv\Scripts\python.exe platform_cli.py plugins enable path/to/my_task.py
.\.venv\Scripts\python.exe platform_cli.py plugins list
```

重启工作台后，新任务会出现在任务下拉框。禁用：`platform_cli.py plugins disable task_id`，代码文件仍保留。
扩展接口、证据与回放要求见TASK_PLUGIN_API_zh.md。插件是普通Python代码，只启用自己审核或信任的文件。

## 公开发布状态

本包是本地生成的可分发技术预览，不代表已经发布到GitHub或网站。
本项目代码的开放许可仍需项目所有者确认；LICENSE.proposed.txt仅为草案。第三方资产许可分别保留在generated/SOURCE_LICENSE和vendor下。
机器人模型的原始上游commit未在早期项目中保存，公开发布前仍需补齐来源核对。robosuite资产已经锁定commit与逐文件hash。
包内不包含用户原始轨迹、私人模型权重、虚拟环境、Hugging Face缓存或凭据。
