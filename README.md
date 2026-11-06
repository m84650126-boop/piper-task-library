# PiPER-L 共享任务库

为 PiPER 本地采集工作台共享**任务代码、场景配置和模型资产**。每个人在自己的电脑上运行仿真，明确选择任务版本；仓库更新不会静默替换正在使用的任务。

仓库：https://github.com/m84650126-boop/piper-task-library

## 使用者：安装共享任务

先安装 PiPER 本地采集工作台。此仓库只分发任务与工作台的共享功能更新，不包含完整机器人/robosuite资产、Python环境、轨迹或模型权重。

如果你使用早期工作台，关闭程序后，将本仓库下载/克隆到本地，再安装共享功能更新：

```powershell
python tools/apply_platform_update.py --platform 'C:\path\to\piper_l_sim'
```

更新前会备份涉及的旧代码，不改动你的 enabled.json、数据和权重。然后在工作台目录执行：

```powershell
.\.venv\Scripts\python.exe platform_cli.py library refresh
.\.venv\Scripts\python.exe platform_cli.py library list
.\.venv\Scripts\python.exe platform_cli.py library install --task shared_reach --version 1.0.0 --enable
.\.venv\Scripts\python.exe collection_workbench.py
```

重启工作台后选择“共享任务示例 · 到达目标”。也可直接下载 packages/shared_reach/1.0.0.zip，通过顶部“任务共享 → 检查 / 安装任务包”安装，离线可用。
任务含 Python 代码，检查和保存阶段不执行代码；明确启用并重启后才加载。
命令行 Git 同步需要安装 Git；ZIP 安装不需要 Git。

## 作者：添加新任务

1. 复制 tasks/shared_reach/1.0.0 到 tasks/<新id>/<版本>。
2. 修改 task_package.json 的 id/version/author/description，同步修改 task.py 的任务 id、环境 task_name、成功判据 execution_unit，以及 scene.json。
3. 添加完整资产，使用相对路径或 XML 的 task-package://assets/...。记录所有资产的来源与许可。
4. 在本机平台执行单条专家、失败样例、保存动作回放及转换读回。任务未通过验证也可提交，但必须明确标记状态。
5. 更新文件哈希并构建目录：

```powershell
python tools/build_catalog.py --seal tasks/my_task/1.0.0
python tools/build_catalog.py
python tools/build_catalog.py --check
```

6. 将源码、ZIP、catalog.json 与 validation/<id>/<版本>.json 一起提交 PR。维护者审阅后合并，其他人刷新任务库即可获取。

同 id/版本的包不可替换内容。代码、场景、成功判据或资产变化必须升级版本。
任务包不等于图形化任务编辑器，新物理任务仍需实现环境/专家/成功判据。
接口目前固定 PiPER-L、7维状态/动作、20Hz、双路320×240 RGB和三个阶段，不适用于任意机械臂。
详细接口与操作见 platform_update/TASK_SHARING_zh.md 和 platform_update/TASK_PLUGIN_API_zh.md。

## 验证状态

catalog.json 记录各版本的检查、专家采集/物理回放/转换状态及证据引用。
“包结构通过”“专家通过”“学习策略闭环通过”是不同结论。示例没有训练好的策略，也没有真机验证。
CI/脚本可验证哈希与分发一致性，不能代替实际物理和策略评测。

项目整体开放许可尚未选定，公开可见不等同于已授予开源许可。模板许可状态明确写入 LICENSE.txt；引入第三方资产时必须附上其许可。
