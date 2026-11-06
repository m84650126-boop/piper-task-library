"""Visible task authoring form; new physics remains an explicit development draft."""
import tkinter as tk
from tkinter import ttk,messagebox
from .task_authoring import create_task
from .tasks import TASKS
from .core import DEFAULT
from .scene_assets import ENVIRONMENTS,OBJECTS


class TaskCreator:
    def __init__(self,app):
        self.app=app
        self.window=tk.Toplevel(app.root);self.window.title("新建原子任务 · 配置变体 / 开发草稿")
        self.window.geometry("790x690");self.window.minsize(650,540)
        self.templates={TASKS[k]["label"]+" · 配置变体":k for k in ("pick_place","push","pull","wipe")}
        self.templates["全新动作逻辑 · 开发草稿（需编程）"]="custom"
        self.fields={k:tk.StringVar(value=v) for k,v in dict(id="my_push",label="我的推送任务",author="本地作者",text="").items()}
        self.template=tk.StringVar(value=next(label for label,key in self.templates.items() if key=="push"))
        self.enable=tk.BooleanVar(value=True);self.note=tk.StringVar()
        book=ttk.Notebook(self.window);book.pack(fill="both",expand=True,padx=12,pady=12)
        basic=ttk.Frame(book,padding=12);scene=ttk.Frame(book,padding=12)
        book.add(basic,text="1 · 名称与动作模板");book.add(scene,text="2 · 场景与随机范围")
        ttk.Label(basic,text="基于现有专家创建变体，无需手工修改XML。",font=("Microsoft YaHei UI",12,"bold")).grid(row=0,column=0,columnspan=2,sticky="w",pady=8)
        ttk.Label(basic,text="动作模板").grid(row=1,column=0,sticky="w")
        selector=ttk.Combobox(basic,textvariable=self.template,values=list(self.templates),state="readonly",width=48)
        selector.grid(row=1,column=1,sticky="ew",pady=6);selector.bind("<<ComboboxSelected>>",self.update_template)
        for row,(key,title) in enumerate((("id","任务ID（英文小写）"),("label","显示名称"),("author","作者"),("text","训练指令（可留空沿用）")),2):
            ttk.Label(basic,text=title).grid(row=row,column=0,sticky="w",padx=(0,10))
            ttk.Entry(basic,textvariable=self.fields[key],width=44).grid(row=row,column=1,sticky="ew",pady=8)
        basic.columnconfigure(1,weight=1)
        ttk.Label(basic,textvariable=self.note,wraplength=650,justify="left").grid(row=6,column=0,columnspan=2,sticky="w",pady=15)
        self.enable_button=ttk.Checkbutton(basic,text="创建并启用（重启工作台后出现在任务列表）",variable=self.enable)
        self.enable_button.grid(row=7,column=0,columnspan=2,sticky="w",pady=8)
        ttk.Label(basic,text="创建仅检查配置。专家试跑、物理回放和数据转换需在创建后验证。\n新建场次只保存一次采集配置，不等于新建任务。",wraplength=650).grid(row=8,column=0,columnspan=2,sticky="w",pady=8)
        self.environment=tk.StringVar(value=ENVIRONMENTS[app.environment_code.get()])
        self.object=tk.StringVar(value=OBJECTS[app.object_code.get()]["label"])
        ttk.Label(scene,text="场景").grid(row=0,column=0,sticky="w")
        ttk.Combobox(scene,textvariable=self.environment,values=list(ENVIRONMENTS.values()),state="readonly",width=44).grid(row=0,column=1,sticky="ew",pady=6)
        ttk.Label(scene,text="物品").grid(row=1,column=0,sticky="w")
        self.objects=ttk.Combobox(scene,textvariable=self.object,values=[v["label"] for v in OBJECTS.values()],state="readonly",width=44)
        self.objects.grid(row=1,column=1,sticky="ew",pady=6)
        self.coordinates={key:tk.StringVar() for key in ("cube_xy","goal_xy","cube_bounds","goal_bounds","min_distance")}
        for row,(key,title) in enumerate((("cube_xy","物品 / 擦拭起点 X,Y"),("goal_xy","目标 / 擦拭终点 X,Y"),("cube_bounds","起点范围 xmin,xmax,ymin,ymax"),("goal_bounds","目标范围 xmin,xmax,ymin,ymax"),("min_distance","起点到目标最小距离")),2):
            ttk.Label(scene,text=title).grid(row=row,column=0,sticky="w",padx=(0,8))
            ttk.Entry(scene,textvariable=self.coordinates[key],width=42).grid(row=row,column=1,sticky="ew",pady=8)
        scene.columnconfigure(1,weight=1)
        ttk.Label(scene,text="单位：米；多个数字用逗号或空格分隔。\n推：+X±30°；拉：−X±30°；推/拉/擦拭距离6～16cm。\n范围内每一条轨迹仍需通过可达性和成功检查。\n此向导选择平台已有物品；自带新网格/纹理需在任务源码中装配。",wraplength=650).grid(row=7,column=0,columnspan=2,sticky="w",pady=15)
        tools=ttk.Frame(self.window,padding=12);tools.pack(fill="x")
        ttk.Button(tools,text="详细步骤",command=app.new_task_help).pack(side="left")
        ttk.Button(tools,text="取消",command=self.window.destroy).pack(side="right",padx=5)
        ttk.Button(tools,text="创建任务与任务包",command=self.submit).pack(side="right")
        self.update_template()

    def update_template(self,event=None):
        base=self.templates[self.template.get()];preset={**DEFAULT,**TASKS.get(base,{}).get("preset",{})}
        for key,value in self.coordinates.items():
            x=preset[key];value.set(", ".join(map(str,x)) if isinstance(x,list) else str(x))
        if base=="custom":
            self.enable.set(False);self.enable_button.configure(state="disabled")
            self.note.set("全新逻辑：生成含接口说明的待开发任务目录。你需要编写场景、启动条件、专家轨迹和成功判据；草稿不会自动启用。")
        else:
            self.enable.set(True);self.enable_button.configure(state="normal")
            self.note.set("配置变体：复用“"+TASKS[base]["label"]+"”的物理、专家、阶段和成功判据。\n"+TASKS[base]["hint"])
        if base=="wipe":self.object.set(OBJECTS["red_cube"]["label"])
        self.objects.configure(state="disabled" if base=="wipe" else "readonly")

    def specification(self):
        def numbers(value):return [float(x) for x in value.replace("，",",").replace(","," ").split()]
        preset={key:(float(value.get()) if key=="min_distance" else numbers(value.get())) for key,value in self.coordinates.items()}
        preset.update(environment=next(k for k,v in ENVIRONMENTS.items() if v==self.environment.get()),
            object=next(k for k,v in OBJECTS.items() if v["label"]==self.object.get()))
        return dict(**{key:value.get() for key,value in self.fields.items()},base_task=self.templates[self.template.get()],preset=preset)

    def submit(self):
        try:
            result=create_task(self.specification(),enable=self.enable.get())
            messagebox.showinfo("原子任务已创建",f"任务ID：{result['id']}\n源码：{result['source']}\n任务包：{result['archive']}\n\n"+
                ("请关闭并重新启动工作台，然后从顶部任务列表选择新任务。\n先执行专家录制1条，再检查和回放。" if result["enabled"] else "任务尚未启用。开发草稿需先完成代码，其他任务可通过任务共享菜单安装启用。"),parent=self.window)
            self.window.destroy()
        except Exception as exc:messagebox.showerror("无法创建任务",str(exc),parent=self.window)
