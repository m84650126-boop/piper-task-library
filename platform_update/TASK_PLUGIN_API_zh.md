# Task API v1：独立添加原子任务

任务扩展是本地单文件Python模块；示例见examples/reach_target.py。它展示了新任务从界面/CLI注册，到录制、成功判断、物理回放和LeRobot导出的完整流程。

## 需要实现的接口

模块声明API_VERSION=1，提供get_task()返回workbench.plugin_api.TaskPlugin：

| 字段/函数 | 职责 |
|---|---|
| id / version / label / text / hint | 稳定任务标识、版本、显示名、训练指令、能力范围 |
| phases | 三个唯一的阶段id及显示名，对应界面1/2/3按钮 |
| preset | 可选默认坐标、区域、距离等配置 |
| make_environment(recipe, render, recorded_xml) | 创建PiPER兼容环境；回放时必须使用给定XML，不偷偷重建另一场景 |
| check_condition(start_xy, goal_xy) | 明确支持的独立启动几何范围；不支持就报错 |
| preflight(env) | 在录制前检查可达性，返回专家计划 |
| expert(env, plan) | 逐步生成(action, stage)，真实物理执行，禁止传送任务对象 |
| evaluate(samples, stages, start_xy, goal_xy) | 纯数据成功判定，返回contract_version、execution_unit、checks、布尔success |
| evidence_keys | 可选额外诊断字段，必须出现在observe()且不覆盖核心状态字段 |
| prepare_recording(env) | 可选人工记录启动检查/诊断重置 |
| restore_initial(env, first_sample) | 可选恢复MuJoCo状态以外的累计诊断，例如覆盖掩码 |

APIv1约束：7维状态/动作（6个关节绝对目标rad+夹爪总开度m）、20Hz、两路320×240 RGB、三个阶段按钮。环境须提供与PiperEnv兼容的reset_scene/observe/step/ik及模型、关节限位等属性；canonical cube_*字段表示参考/操作对象的诊断状态。
更换机械臂、任意维度动作、深度或多对象状态需要另行扩展机器人/数据适配器，不能只改任务名称。

## 数据与版本

仅task_plugins/enabled.json中明确启用且SHA256匹配的代码会加载，不自动执行目录中所有.py文件。
每条记录保存任务id、版本、源文件hash、场景XML、初始/目标坐标、相机、状态、动作和诊断。代码变更后不能静默用新成功判据重判旧数据；需保留并重新启用当时版本，或明确发布新的数据版本。
APIv1自动追溯范围是单文件插件；额外辅助代码、外部资产和依赖必须另行固定版本。
新增任务包方式支持多文件源码、配置和资产逐文件校验，元数据增加package_sha256；详细使用见TASK_SHARING_zh.md。
成功判据必须证明实际结果。夹爪闭合不是抓住；到达动作终点不是推拉成功；空中运动不是擦拭。
学习策略的独立启动/连续交接仍需单独评测，不能从脚本演示通过自动升级能力注册状态。

## 仿真与转换环境分离

评价函数应仅依赖保存的证据和NumPy；MuJoCo、SciPy等仿真依赖放在工厂/规划函数内延迟导入。这样同一插件可在精简LeRobot转换环境重新判定。
若插件顶层导入额外库，转换环境也必须安装相应依赖；不要让数据转换隐式下载模型或访问网络。

## 建议验证顺序

先用doctor确认环境；单条专家试跑；保存动作物理回放；检查失败/越界样例会失败；采集多个空间组；转换读回；核对固定划分与训练集统计量。状态型诊断必须在回放中重算并比较。
当前reach_target示例已跑通上述采集/回放/导出链路，但不是训练好的策略。
