# 任务包与共享任务库

共享仓库：https://github.com/m84650126-boop/piper-task-library

本机工作台仍通过 collection_workbench.py 启动。顶部新增“任务共享”菜单：
选择“检查 / 安装任务包”，先查看作者、版本、文件哈希，再选择是否信任并启用。
启用后关闭并重启工作台，新任务会出现在任务下拉框。
另一项“从任务源目录导出”将已完成封装的任务目录导出为 ZIP。
此功能不是图形化任务编程器；新的物理任务仍需作者实现控制器和成功判据。

## 普通使用者

下载任务 ZIP 后，可以使用菜单安装，也可以在 PowerShell 运行：

```powershell
cd 'C:\Users\Lenovo\Documents\Codexdesign\piper_l_sim'
.\.venv\Scripts\python.exe platform_cli.py packages inspect path\to\task.zip
.\.venv\Scripts\python.exe platform_cli.py packages install path\to\task.zip --enable
.\.venv\Scripts\python.exe collection_workbench.py
```

不加 --enable 则只保存、暂不启用。任务包含 Python 代码，只启用信任的来源。
导入检查不执行代码，也不会自动安装依赖。新增依赖需分别配置采集和转换环境。

## 从统一 Git 任务库获取

电脑需要安装 Git。首次刷新会建立独立缓存，后续刷新获取 main 最新目录。
刷新不会替换当前启用的任务。每次安装明确选择版本，并记录仓库 commit 和包哈希。

```powershell
.\.venv\Scripts\python.exe platform_cli.py library refresh
.\.venv\Scripts\python.exe platform_cli.py library list
.\.venv\Scripts\python.exe platform_cli.py library install --task shared_reach --version 1.0.0 --enable
```

获取成功后重启工作台即可选择“共享任务示例 · 到达目标”。
同一任务同时启用一个版本。升级前先 `platform_cli.py plugins disable shared_reach`，再安装选定版本。
旧版本文件留在 task_packages 下；历史数据转换/回放需要启用其原始版本。
本版本的在线浏览/刷新使用上述 CLI；GUI 提供本地 ZIP 安装/导出与使用说明。
现有老版平台安装包没有任务包功能，其他人需获取本次更新后的平台。

## 创建新任务

复制 examples/shared_reach 为独立目录，包括：

```text
my_task/
  task_package.json    # id、版本、作者、兼容 API、文件哈希
  task.py             # 场景、专家、阶段和纯数据成功判据
  scene.json          # 默认坐标/随机范围、任务专用参数
  assets/             # XML、网格、纹理等全部自带资产
  README.md
  LICENSE.txt
```

1. 修改 task_package.json 的 id/version/author/description。
2. 同步修改 task.py 中任务 id、环境 task_name、evaluate 返回的 execution_unit 和版本。
3. 修改 scene.json 的 task、默认采集范围和任务参数；TaskPlugin.preset 用于工作台默认值。
4. 在 make_environment 中读取并装配包内场景。scene.json 不会自动创建任意 MuJoCo 场景。
5. 资产使用相对于任务包的路径。XML可使用 `task-package://assets/model.stl`；
   调用 resolve_asset_references 仅在编译模型时解析，env.source_xml 保留未展开路径，供异机回放。
   包内辅助 Python 文件可用 `from .helper import ...`；外部 import 不会被自动打包。
6. 每次代码、配置或资产变化都升级版本，再显式更新清单、导出：

```powershell
.\.venv\Scripts\python.exe platform_cli.py packages seal path\to\my_task
.\.venv\Scripts\python.exe platform_cli.py packages export path\to\my_task --output path\to\my_task_1.0.0.zip
```

导出拒绝未登记文件、哈希不一致、外部符号链接和不支持的载荷。
安装拒绝路径穿越、重复路径、过大的 ZIP、资产篡改及同版本不同内容。
哈希提供一致性检查，不是作者身份认证；仓库代码评审仍由维护者负责。
作者负责把所有引用的辅助代码/资产纳入包，列出来源、许可及平台内复用资产。
任务包无需重新打包整套机器人模型；接收者的基础平台也必须与任务兼容。
当前 API 固定 PiPER-L、7维动作、20Hz、两路320×240 RGB和三个阶段。

## 提交到共享仓库

仓库保存 tasks/<id>/<version>/ 源目录、packages/<id>/<version>.zip、catalog.json、验证记录。
从仓库提供的 tools/build_catalog.py 重建包和目录索引，再提交 PR。
CI 检查包哈希和内容对应关系；物理验证应由维护者在平台完成，并提供真实记录。
“可导入”“专家/回放通过”“学习策略闭环通过”分别记录，不自动互相升级。
本次 shared_reach 是专家采集示例，不是训练好的策略。

数据记录保留任务版本、入口源码哈希、整个包的清单哈希、场景 XML 和实际采集参数。
同一空间分组的训练/验证/测试划分继续沿用平台固定协议，不随任务库刷新改变。
请勿向共享任务库上传用户原始轨迹、私人权重、环境、凭据或无权分发的资产。

项目整体开放许可尚未选定，模板中的 LICENSE.txt 明确记录此状态；仓库可见性不等于已授予开源许可。
