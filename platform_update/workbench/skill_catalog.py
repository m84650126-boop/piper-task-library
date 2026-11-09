"""Requested skills and their availability in the collection workbench."""

# The requested skill IDs are distinct from implementation IDs in saved episodes.
SKILLS = (
    ("pick", "抓取", "Pick", "物体操作", "pick", ""),
    ("place", "放置", "Place", "物体操作", "place", ""),
    ("handover", "交接", "Handover", "物体操作", None, "双臂或接收工装，验证载荷从供方转移至接收方。"),
    ("move", "移动", "Move", "物体操作", "move_held_object", ""),
    ("open", "打开", "Open", "机构操作", "open", ""),
    ("close", "关闭", "Close", "机构操作", "close", ""),
    ("push", "推移", "Push", "物体操作", "push", ""),
    ("pull", "拉动", "Pull", "物体操作", "pull", ""),
    ("press", "按压", "Press", "机构操作", "press", ""),
    ("rotate", "旋转", "Rotate", "机构操作", "rotate", ""),
    ("screw", "旋拧", "Screw", "机构操作", None, "螺纹耦合、啮合深度和扭矩判据。"),
    ("plug", "插接", "Plug", "机构操作", None, "按尺寸建模的插头与插座，检查对中、深度及插接力。"),
    ("pour", "倾倒", "Pour", "介质操作", None, "容器与可迁移介质，量化接收量和溢出。"),
    ("fill", "填充", "Fill", "介质操作", None, "介质来源和容积计量，验证目标填充量。"),
    ("scoop", "舀取", "Scoop", "介质操作", None, "勺与可舀取介质，记录舀出和留存量。"),
    ("stir", "搅拌", "Stir", "介质操作", None, "搅拌工具和介质，检查浸入与混合效果。"),
    ("dip", "浸蘸", "Dip", "介质操作", None, "液面与浸入深度，检查浸蘸时长及带出。"),
    ("shake", "摇晃", "Shake", "物体操作", "shake", ""),
    ("fold", "折叠与展开", "Fold", "柔性与加工", None, "可变形片材、折线和折后形状判据。"),
    ("tie", "系结", "Tie", "柔性与加工", None, "具自碰撞的绳索与结型判定。"),
    ("wipe", "擦拭", "Wipe", "表面处理", "wipe", ""),
    ("sweep", "扫拢", "Sweep", "表面处理", None, "扫具与离散颗粒，测量聚集比例。"),
    ("wash", "清洗", "Wash", "表面处理", None, "清洗介质与可移除附着物，量化残留。"),
    ("brush", "刷拭", "Brush", "表面处理", None, "刷毛或可标定接触模型，测量有效覆盖。"),
    ("cut", "切割", "Cut", "柔性与加工", None, "材料断裂或可验证的切割拓扑，测量切入与分离。"),
    ("peel", "剥皮", "Peel", "柔性与加工", None, "可分离外层、剥离力和剩余面积。"),
    ("strike", "敲击", "Strike", "动态与感知", None, "工具和可测冲量的目标，检查命中和峰值力。"),
    ("scan", "扫描", "Scan", "动态与感知", None, "可识别的标记或工件、视线遮挡和实际识别结果。"),
    ("throw", "抛掷", "Throw", "动态与感知", None, "释放、自由飞行及落点证据。"),
)


def entries(tasks):
    return [
        dict(skill=skill, label=label, english=english, family=family, task=task,
             status="available" if task in tasks else "pending",
             detail=tasks[task]["hint"] if task in tasks else required)
        for skill, label, english, family, task, required in SKILLS
    ]
