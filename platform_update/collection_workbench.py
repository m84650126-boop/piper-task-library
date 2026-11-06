"""PiPER-L desktop collection workbench. Run with the simulation .venv Python."""
import argparse
import json
import multiprocessing as mp
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import ttk,messagebox,filedialog
from workbench.core import (ROOT,DEFAULT,validate_recipe,new_session,catalog,
                            trash_episode,safe_episode,spatial_group)
from workbench.worker import run
from workbench.tasks import TASKS
from workbench.scene_assets import ENVIRONMENTS,OBJECTS


def desktop_root():
    # Avoid Windows' bitmap DPI scaling: logical screenshots/coordinates must
    # describe the actual visible window, including on150% laptop displays.
    if sys.platform=="win32":
        import ctypes
        try:ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError,OSError):ctypes.windll.user32.SetProcessDPIAware()
    root=tk.Tk()
    root.tk.call("tk","scaling",1.25)
    return root


class Workbench:
    def __init__(self,root,session=None,start_worker=True):
        self.root=root
        root.title("PiPER-L 本地采集工作台 · 多任务")
        width=min(1420,root.winfo_screenwidth()-60)
        height=min(920,root.winfo_screenheight()-90)
        root.geometry(f"{width}x{height}+20+30")
        root.minsize(min(1120,width),min(740,height))
        self.session=Path(session) if session else None
        self.process=None
        self.mode="idle"
        self.job=None
        self.rows=[]
        self.drag=None
        self.preview=None
        self.preview_after=None
        self.actual=None
        self.photos=[]
        self.closing=False
        self.context=mp.get_context("spawn")
        self.commands=self.context.Queue()
        self.events=self.context.Queue()
        self.frames=self.context.Queue(maxsize=2)
        self.values={}
        self.task_code=tk.StringVar(value="pick_place")
        self.task_label=tk.StringVar(value=TASKS["pick_place"]["label"])
        self.environment_code=tk.StringVar(value="basic");self.environment_label=tk.StringVar(value=ENVIRONMENTS["basic"])
        self.object_code=tk.StringVar(value="red_cube");self.object_label=tk.StringVar(value=OBJECTS["red_cube"]["label"])
        self.task_hint=tk.StringVar()
        self.scene_help=tk.StringVar()
        self.status=tk.StringVar(value="正在准备本地模拟器…")
        self.session_text=tk.StringVar()
        self.count_text=tk.StringVar(value="完整轨迹 0 | 成功 0 | 失败 0")
        self.live_text=tk.StringVar(value="双相机初始化中")
        self.scope_text=tk.StringVar(value="空间划分：5cm起点格×目标格，按组合固定80/10/10；条数比例可能不同")
        self.build()
        recipe={**DEFAULT,"environment":"robosuite_lab"}
        if self.session:
            recipe=json.loads((self.session/"recipe.json").read_text(encoding="utf-8"))
        self.set_recipe(recipe)
        if start_worker:
            if not self.session:self.session=new_session(recipe)
            self.start_sim(recipe)
        self.refresh()
        root.bind("<KeyPress>",self.on_key)
        root.protocol("WM_DELETE_WINDOW",self.close)
        root.after(70,self.poll)

    def build(self):
        style=ttk.Style()
        style.theme_use("clam")
        style.configure("TButton",padding=(8,5))
        style.configure("Title.TLabel",font=("Microsoft YaHei UI",15,"bold"))
        header=ttk.Frame(self.root,padding=10);header.pack(fill="x")
        ttk.Label(header,text="PiPER-L 本地采集工作台",style="Title.TLabel").pack(side="left")
        ttk.Label(header,text="  任务：").pack(side="left")
        selector=ttk.Combobox(header,textvariable=self.task_label,values=[v["label"] for v in TASKS.values()],state="readonly",width=24)
        selector.pack(side="left");selector.bind("<<ComboboxSelected>>",self.change_task)
        ttk.Label(header,text="  双RGB · 20Hz").pack(side="left")
        ttk.Button(header,text="新建场次",command=self.new).pack(side="right",padx=4)
        ttk.Button(header,text="打开场次",command=self.open_session).pack(side="right")
        menu=tk.Menu(self.root);packages=tk.Menu(menu,tearoff=False)
        packages.add_command(label="检查 / 安装任务包…",command=self.import_task_package)
        packages.add_command(label="从任务源目录导出…",command=self.export_task_package)
        packages.add_command(label="查看共享任务库说明",command=self.task_library_help)
        menu.add_cascade(label="任务共享",menu=packages);self.root.configure(menu=menu)
        ttk.Label(self.root,textvariable=self.session_text,padding=(10,0)).pack(anchor="w")
        main=ttk.Panedwindow(self.root,orient="horizontal");main.pack(fill="both",expand=True,padx=10,pady=8)
        left_outer=ttk.Frame(main,width=350);right=ttk.Frame(main)
        main.add(left_outer,weight=0);main.add(right,weight=1)
        left_canvas=tk.Canvas(left_outer,width=345,highlightthickness=0)
        left_scroll=ttk.Scrollbar(left_outer,orient="vertical",command=left_canvas.yview)
        left_canvas.configure(yscrollcommand=left_scroll.set)
        left_scroll.pack(side="right",fill="y");left_canvas.pack(side="left",fill="both",expand=True)
        left=ttk.Frame(left_canvas)
        left_window=left_canvas.create_window((0,0),window=left,anchor="nw")
        left.bind("<Configure>",lambda e:left_canvas.configure(scrollregion=left_canvas.bbox("all")))
        left_canvas.bind("<Configure>",lambda e:left_canvas.itemconfigure(left_window,width=e.width))
        def scroll_controls(event):
            widget=self.root.winfo_containing(event.x_root,event.y_root)
            while widget is not None and widget is not left_outer:widget=getattr(widget,"master",None)
            if widget is left_outer and event.delta:left_canvas.yview_scroll(-1 if event.delta>0 else 1,"units")
        self.root.bind_all("<MouseWheel>",scroll_controls,add="+")
        resources=ttk.LabelFrame(left,text="场景与物品 · 官方仿真资产",padding=8);resources.pack(fill="x",pady=(0,5))
        for row,(text,variable,choices) in enumerate((("场景",self.environment_label,list(ENVIRONMENTS.values())),
                 ("物品",self.object_label,[v["label"] for v in OBJECTS.values()]))):
            ttk.Label(resources,text=text).grid(row=row,column=0,sticky="w")
            combo=ttk.Combobox(resources,textvariable=variable,values=choices,state="readonly",width=26)
            combo.grid(row=row,column=1,pady=3,sticky="ew");combo.bind("<<ComboboxSelected>>",self.change_assets)
            if row==1:self.object_selector=combo
        ttk.Label(resources,text="robosuite v1.5.2场景/网格/纹理；PiPER-L控制。\n活动物品统一高4cm、45g，保留原始比例。",wraplength=310,foreground="#245579").grid(row=2,column=0,columnspan=2,sticky="w")
        ttk.Button(resources,text="打开 / 关闭3D场景浏览器",command=lambda:self.send(op="viewer")).grid(row=3,column=0,columnspan=2,sticky="ew",pady=4)
        scene=ttk.LabelFrame(left,text="1  场景设置（单位：米）",padding=8);scene.pack(fill="x")
        for row,(key,title) in enumerate((("cube_xy","红方块 X / Y"),("goal_xy","绿目标 X / Y"))):
            label=ttk.Label(scene,text=title);label.grid(row=row,column=0,sticky="w")
            if key=="cube_xy":self.start_label=label
            else:self.goal_label=label
            self.entries(scene,key,2,row,1)
        ttk.Button(scene,text="应用坐标 / 重置场景",command=self.apply).grid(row=2,column=0,columnspan=3,sticky="ew",pady=5)
        ttk.Label(scene,textvariable=self.scene_help,foreground="#5e6470").grid(row=3,column=0,columnspan=3,sticky="w")
        ttk.Label(scene,textvariable=self.task_hint,wraplength=310,foreground="#245579").grid(row=4,column=0,columnspan=3,sticky="w",pady=4)
        random_box=ttk.LabelFrame(left,text="2  自动批采范围（独立随机起点与目标）",padding=8);random_box.pack(fill="x",pady=6)
        for i,(key,label) in enumerate((("cube_bounds","起点区域"),("goal_bounds","目标区域"))):
            ttk.Label(random_box,text=label+" xmin / xmax / ymin / ymax").grid(row=i*2,column=0,columnspan=4,sticky="w")
            self.entries(random_box,key,4,i*2+1,0,width=7)
        for i,(key,label) in enumerate((("min_distance","最小间距"),("seed","起始seed"),("count","轨迹数量")),4):
            ttk.Label(random_box,text=label).grid(row=i,column=0,columnspan=2,sticky="w")
            self.entries(random_box,key,1,i,2,width=12)
        self.sample_mode=tk.StringVar(value="random")
        ttk.Radiobutton(random_box,text="区域随机",variable=self.sample_mode,value="random").grid(row=7,column=0,columnspan=2,sticky="w")
        ttk.Radiobutton(random_box,text="固定坐标",variable=self.sample_mode,value="fixed").grid(row=7,column=2,columnspan=2,sticky="w")
        ttk.Button(random_box,text="开始自动批采",command=lambda:self.send_recipe("batch")).grid(row=8,column=0,columnspan=2,sticky="ew",pady=4)
        ttk.Button(random_box,text="完成当前条后停止",command=lambda:self.send(op="stop_batch")).grid(row=8,column=2,columnspan=2,sticky="ew")
        manual=ttk.LabelFrame(left,text="3  人工示范",padding=8);manual.pack(fill="x")
        self.record_button=ttk.Button(manual,text="开始 / 保存录制  [Enter]",command=lambda:self.send(op="record"))
        self.record_button.pack(fill="x")
        ttk.Button(manual,text="按当前摆放执行专家 · 录制1条",command=self.expert_once).pack(fill="x",pady=3)
        ttk.Button(manual,text="放弃当前录制  [X]",command=lambda:self.send(op="discard")).pack(fill="x",pady=3)
        ttk.Label(manual,text="W/S：X±   A/D：Y±   R/F：Z±（每次5mm）\nO：开爪   C：合爪   N：应用坐标重置\n点击“键盘控制区”后操作，编辑框内不触发运动。",justify="left").pack(anchor="w",pady=3)
        self.key_area=tk.Label(manual,text="点击这里启用键盘控制",background="#dce8f5",foreground="#18334f",padx=5,pady=8,takefocus=True)
        self.key_area.pack(fill="x")
        self.key_area.bind("<Button-1>",lambda e:self.key_area.focus_set())
        phases=ttk.Frame(manual);phases.pack(fill="x",pady=5)
        self.phase_buttons=[]
        for i in range(3):
            button=ttk.Button(phases,text=str(i+1),command=lambda n=i:self.select_phase(n))
            button.pack(side="left",expand=True,fill="x");self.phase_buttons.append(button)
        ttk.Label(manual,text="阶段按钮只作标注，不驱动机械臂。\n每种任务使用独立契约，请查看操作说明。",foreground="#5e6470").pack(anchor="w")
        ttk.Button(left,text="打开操作说明",command=self.help).pack(fill="x",pady=6)
        views=ttk.LabelFrame(right,text="双相机实时画面 / 数据预览",padding=5);views.pack(fill="x")
        self.image_labels=[]
        camera_row=ttk.Frame(views);camera_row.pack()
        for name in ("固定相机 overview","腕部相机 wrist"):
            box=ttk.Frame(camera_row);box.pack(side="left",padx=3)
            ttk.Label(box,text=name).pack()
            placeholder=tk.PhotoImage(width=320,height=240)
            image=tk.Label(box,image=placeholder,width=320,height=240,background="#18232e")
            image.placeholder=placeholder
            image.pack();self.image_labels.append(image)
        ttk.Label(views,textvariable=self.live_text,wraplength=740).pack(anchor="w",pady=4)
        map_box=ttk.LabelFrame(right,text="场景平面与已采集覆盖：拖动起点 / 目标；小点为已录轨迹",padding=5);map_box.pack(fill="both",expand=True,pady=6)
        self.canvas=tk.Canvas(map_box,height=170,background="#f3f6f9",highlightthickness=0)
        self.canvas.pack(fill="both",expand=True)
        self.canvas.bind("<Configure>",lambda e:self.draw_map())
        self.canvas.bind("<ButtonPress-1>",self.drag_start)
        self.canvas.bind("<B1-Motion>",self.drag_move)
        self.canvas.bind("<ButtonRelease-1>",self.drag_end)
        ttk.Label(map_box,textvariable=self.scope_text,foreground="#5e6470",wraplength=740).pack(anchor="w")
        data=ttk.LabelFrame(right,text="4  数据审核与导出",padding=5);data.pack(fill="both",expand=True)
        tools=ttk.Frame(data);tools.pack(fill="x")
        ttk.Label(tools,textvariable=self.count_text).pack(side="left")
        for label,command in (("刷新",self.refresh),("预览",self.start_preview),("移入回收站",self.delete),
                              ("检查",lambda:self.start_job("check")),("检查并导出",lambda:self.start_job("export"))):
            ttk.Button(tools,text=label,command=command).pack(side="left",padx=2)
        tablebox=ttk.Frame(data);tablebox.pack(fill="both",expand=True,pady=4)
        columns=("result","frames","seed","start","goal","split","source")
        self.table=ttk.Treeview(tablebox,columns=columns,show="headings",height=4,selectmode="browse")
        for key,title,width in zip(columns,("结果","帧数","seed","物体XY","目标XY","集合","来源"),(55,50,65,110,110,70,65)):
            self.table.heading(key,text=title);self.table.column(key,width=width,minwidth=40)
        self.table.pack(side="left",fill="both",expand=True)
        scroll=ttk.Scrollbar(tablebox,orient="vertical",command=self.table.yview);scroll.pack(side="right",fill="y")
        self.table.configure(yscrollcommand=scroll.set)
        self.table.bind("<Double-1>",lambda e:self.start_preview())
        self.log=tk.Text(data,height=3,wrap="word",font=("Consolas",9),state="disabled")
        self.log.pack(fill="x")
        ttk.Label(self.root,textvariable=self.status,relief="sunken",padding=7).pack(fill="x")

    def entries(self,parent,key,count,row,column,width=9):
        values=[]
        for i in range(count):
            v=tk.StringVar();values.append(v)
            ttk.Entry(parent,textvariable=v,width=width).grid(row=row,column=column+i,padx=2,pady=3,sticky="ew")
        self.values[key]=values

    def recipe(self):
        r={**DEFAULT,"mode":self.sample_mode.get(),"task":self.task_code.get(),
           "environment":self.environment_code.get(),"object":self.object_code.get()}
        for key,values in self.values.items():
            items=[float(v.get()) for v in values]
            r[key]=int(items[0]) if key in ("seed","count") else items[0] if len(items)==1 else items
        return validate_recipe(r)

    def set_recipe(self,r):
        task=r.get("task","pick_place");self.task_code.set(task);self.task_label.set(TASKS[task]["label"])
        environment=r.get("environment","basic");obj=r.get("object","red_cube")
        self.environment_code.set(environment);self.environment_label.set(ENVIRONMENTS[environment])
        self.object_code.set(obj);self.object_label.set(OBJECTS[obj]["label"])
        self.object_selector.configure(state="disabled" if task=="wipe" else "readonly")
        self.task_hint.set(TASKS[task]["hint"])
        self.scene_help.set("拖动红/绿端点设置擦拭带；松开鼠标应用。\n录制/批采时锁定场景。" if task=="wipe" else "拖动红色物体或绿色目标；松开鼠标应用。\n录制/批采时锁定场景。")
        self.start_label.configure(text="擦拭起点 X/Y" if task=="wipe" else "物品起点 X/Y")
        self.goal_label.configure(text="擦拭终点 X/Y" if task=="wipe" else "绿目标 X / Y")
        self.table.heading("start",text="擦拭起点XY" if task=="wipe" else "物体XY")
        self.table.heading("goal",text="擦拭终点XY" if task=="wipe" else "目标XY")
        for button,(_,label) in zip(self.phase_buttons,TASKS[task]["phases"]):button.configure(text=label)
        for key,values in self.values.items():
            raw=r[key] if isinstance(r[key],list) else [r[key]]
            for v,x in zip(values,raw):v.set(str(x))
        self.sample_mode.set(r.get("mode","random"))

    def select_phase(self,index):self.send(op="phase",value=TASKS[self.task_code.get()]["phases"][index][0])

    def change_assets(self,event=None):
        environment=next(k for k,v in ENVIRONMENTS.items() if v==self.environment_label.get())
        obj=next(k for k,v in OBJECTS.items() if v["label"]==self.object_label.get())
        previous=json.loads((self.session/"recipe.json").read_text(encoding="utf-8"))
        if self.mode not in ("idle","error") or self.job:
            self.environment_label.set(ENVIRONMENTS[self.environment_code.get()]);self.object_label.set(OBJECTS[self.object_code.get()]["label"])
            messagebox.showinfo("场景锁定","请先结束当前录制、批采或导出")
            return
        try:
            r={**self.recipe(),"environment":environment,"object":obj}
            self.switch(new_session(r))
        except Exception as exc:
            self.set_recipe(previous);messagebox.showerror("场景切换失败",str(exc))

    def expert_once(self):
        if self.mode!="idle" or self.job:
            messagebox.showinfo("当前忙碌","请先结束当前任务");return
        try:
            r={**self.recipe(),"mode":"fixed","count":1}
            self.send(op="batch",recipe=r)
        except Exception as exc:messagebox.showerror("专家未启动",str(exc))

    def change_task(self,event=None):
        selected=next(k for k,v in TASKS.items() if v["label"]==self.task_label.get())
        if selected==self.task_code.get():return
        if self.mode not in ("idle","error") or self.job:
            self.task_label.set(TASKS[self.task_code.get()]["label"])
            messagebox.showinfo("任务锁定","先保存/放弃当前录制，或停止批采/等待导出完成")
            return
        try:
            r={**DEFAULT,**TASKS[selected].get("preset",{}),"task":selected,
                "environment":self.environment_code.get(),"object":self.object_code.get() if selected!="wipe" else "red_cube"}
            self.switch(new_session(r))
        except Exception as exc:
            self.task_label.set(TASKS[self.task_code.get()]["label"])
            messagebox.showerror("任务切换失败",str(exc))

    def logline(self,text):
        self.log.configure(state="normal");self.log.insert("end",text+"\n");self.log.see("end")
        # Long batch/encoding logs stay bounded in the widget, full job log is on disk.
        if int(self.log.index("end-1c").split(".")[0])>500:self.log.delete("1.0","100.0")
        self.log.configure(state="disabled")

    def start_sim(self,r):
        self.commands=self.context.Queue();self.frames=self.context.Queue(maxsize=2);self.events=self.context.Queue()
        self.process=self.context.Process(target=run,args=(self.commands,self.events,self.frames,str(self.session),r),daemon=True)
        self.process.start()
        self.session_text.set("当前场次："+str(self.session))
        self.mode="starting"
        self.status.set("正在初始化："+TASKS[r.get("task","pick_place")]["label"])

    def send(self,**command):
        if not self.process or not self.process.is_alive():
            self.status.set("模拟器未运行，请查看日志或重新打开场次")
            return
        if self.job:
            self.status.set("检查/转换中已锁定采集，完成后可继续")
            return
        self.stop_preview()
        self.commands.put(command)

    def send_recipe(self,op):
        try:
            if self.mode!="idle":raise ValueError("请先结束当前录制/批采，并等待模拟器就绪")
            r=self.recipe()
            (self.session/"recipe.json").write_text(json.dumps(r,indent=2),encoding="utf-8")
            self.send(op=op,recipe=r)
        except (ValueError,TypeError) as exc:messagebox.showerror("设置未应用",str(exc))

    def apply(self):self.send_recipe("reset")

    def on_key(self,event):
        if isinstance(event.widget,(tk.Entry,ttk.Entry,tk.Text,ttk.Combobox,tk.Spinbox)):return
        if event.widget not in (self.key_area,self.canvas):return
        if self.preview:return
        key=event.keysym.upper()
        moves={"W":[.005,0,0],"S":[-.005,0,0],"A":[0,.005,0],"D":[0,-.005,0],"R":[0,0,.005],"F":[0,0,-.005]}
        if key in moves:self.send(op="move",delta=moves[key])
        elif key in ("O","C"):self.send(op="gripper",aperture=.09 if key=="O" else 0.)
        elif key in ("RETURN","KP_ENTER"):self.send(op="record")
        elif key=="X":self.send(op="discard")
        elif key=="N":self.apply()
        elif key in ("1","2","3"):self.select_phase(int(key)-1)

    def xy_to_screen(self,xy):
        w=max(100,self.canvas.winfo_width());h=max(100,self.canvas.winfo_height())
        return 42+(xy[0]-.15)/.45*(w-62),h-28-(xy[1]+.30)/.60*(h-45)

    def screen_to_xy(self,x,y):
        w=max(100,self.canvas.winfo_width());h=max(100,self.canvas.winfo_height())
        return [round(max(.18,min(.55,.15+(x-42)/(w-62)*.45)),3),round(max(-.30,min(.30,-.30+(h-28-y)/(h-45)*.60)),3)]

    def draw_map(self):
        c=self.canvas;c.delete("all")
        try:r=self.recipe()
        except (ValueError,TypeError):return
        for i in range(4,12):
            x=i*.05;a=self.xy_to_screen([x,-.3]);b=self.xy_to_screen([x,.3])
            c.create_line(*a,*b,fill="#dce3e9");c.create_text(a[0],a[1]+13,text=f"{x:.2f}",font=("Arial",8))
        for i in range(-3,4):
            y=i*.1;a=self.xy_to_screen([.15,y]);b=self.xy_to_screen([.60,y])
            c.create_line(*a,*b,fill="#dce3e9");c.create_text(a[0]-19,a[1],text=f"{y:.1f}",font=("Arial",8))
        for key,color in (("cube_bounds","#e3a2a2"),("goal_bounds","#8ebfa0")):
            b=r[key];a=self.xy_to_screen([b[0],b[2]]);z=self.xy_to_screen([b[1],b[3]])
            c.create_rectangle(*a,*z,outline=color,dash=(4,3),width=2)
        for row in self.rows:
            color={"train":"#3b82bc","validation":"#a47bca","test":"#d28a25"}[row["split"]]
            a=self.xy_to_screen(row["cube_xy"]);b=self.xy_to_screen(row["goal_xy"])
            c.create_oval(a[0]-2,a[1]-2,a[0]+2,a[1]+2,fill=color,outline=color)
            c.create_rectangle(b[0]-2,b[1]-2,b[0]+2,b[1]+2,outline=color)
        x,y=self.xy_to_screen(r["goal_xy"]);c.create_rectangle(x-10,y-10,x+10,y+10,outline="#13945c",fill="#b4ebc7",width=2)
        c.create_text(x+13,y-10,text="目标",anchor="w",fill="#087244")
        x,y=self.xy_to_screen(r["cube_xy"]);c.create_oval(x-8,y-8,x+8,y+8,outline="#b73232",fill="#ed6d60",width=2)
        c.create_text(x+12,y+8,text="起点",anchor="w",fill="#9e2929")
        if self.actual:
            x,y=self.xy_to_screen(self.actual["cube"][:2]);c.create_oval(x-4,y-4,x+4,y+4,outline="#111827",width=2)
            x,y=self.xy_to_screen(self.actual["tcp"][:2]);c.create_text(x,y,text="+",fill="#1d354e",font=("Arial",16))
        if self.task_code.get()=="wipe":
            import math
            a,b=r["cube_xy"],r["goal_xy"];dx,dy=b[0]-a[0],b[1]-a[1];length=math.hypot(dx,dy)
            if length>0:
                n=[-.03*dy/length,.03*dx/length]
                corners=[[p[0]+s*n[0],p[1]+s*n[1]] for p,s in ((a,-1),(b,-1),(b,1),(a,1))]
                c.create_polygon(*[v for p in corners for v in self.xy_to_screen(p)],outline="#178cbe",fill="",width=2)
        legend="圆点=带起点 / 方点=带终点" if self.task_code.get()=="wipe" else "圆点=物体起点 / 方点=目标"
        c.create_text(8,8,text="Y↑   X→  |  "+legend+"  |  蓝=训练 紫=验证 橙=测试",anchor="nw",fill="#48576a",font=("Microsoft YaHei UI",9))

    def drag_start(self,event):
        if self.mode!="idle" or self.job:return
        try:r=self.recipe()
        except ValueError:return
        candidates=[]
        for key in ("cube_xy","goal_xy"):
            x,y=self.xy_to_screen(r[key]);candidates.append(((x-event.x)**2+(y-event.y)**2,key))
        distance,key=min(candidates)
        if distance<600:self.drag=key

    def drag_move(self,event):
        if not self.drag:return
        for v,x in zip(self.values[self.drag],self.screen_to_xy(event.x,event.y)):v.set(str(x))
        self.draw_map()

    def drag_end(self,event):
        if self.drag:self.drag=None;self.apply()

    def refresh(self):
        selected=self.table.selection() if hasattr(self,"table") else ()
        self.rows=catalog(self.session)
        self.table.delete(*self.table.get_children())
        for row in self.rows:
            fmt=lambda a:f"{a[0]:.3f}, {a[1]:.3f}"
            self.table.insert("","end",iid=row["name"],values=("成功" if row["success"] else "失败",row["frames"],row["seed"],fmt(row["cube_xy"]),fmt(row["goal_xy"]),row["split"],row["source"]))
        if selected and self.table.exists(selected[0]):self.table.selection_set(selected)
        counts={k:sum(r["split"]==k for r in self.rows if r["success"]) for k in ("train","validation","test")}
        n=sum(r["success"] for r in self.rows)
        unique=len({r["condition_id"] for r in self.rows})
        self.count_text.set(f"完整 {len(self.rows)} | 成功 {n} | 失败 {len(self.rows)-n} | 唯一条件 {unique}")
        self.scope_text.set(f"成功轨迹空间集合：训练 {counts['train']} / 验证 {counts['validation']} / 测试 {counts['test']}；固定5cm起点×目标组合，条数比例可不同")
        self.draw_map()

    def selected(self):
        names=self.table.selection()
        if not names:raise ValueError("请先选择一条轨迹")
        return names[0]

    def delete(self):
        if self.mode!="idle" or self.job:
            messagebox.showinfo("数据锁定","请先结束录制/批采/检查")
            return
        try:
            name=self.selected()
            if not messagebox.askyesno("移入回收站",f"将 {name} 移到当前场次trash目录？\n文件仍可从该目录手动恢复；已有导出快照不会改变。") :return
            self.stop_preview();target=trash_episode(self.session,name)
            self.logline("已移入回收站："+str(target));self.refresh()
        except Exception as exc:messagebox.showerror("操作失败",str(exc))

    def show_images(self,images):
        self.photos=[tk.PhotoImage(data=image) for image in images]
        for label,photo in zip(self.image_labels,self.photos):label.configure(image=photo,width=320,height=240)

    def start_preview(self):
        if self.mode!="idle" or self.job:return
        try:
            import h5py
            name=self.selected();self.stop_preview()
            self.preview=h5py.File(safe_episode(self.session,name),"r")
            self.preview_index=0
            self.preview_tick()
        except Exception as exc:messagebox.showerror("预览失败",str(exc))

    def preview_tick(self):
        if not self.preview:return
        import cv2
        f=self.preview;i=self.preview_index
        images=[cv2.imencode(".png",cv2.cvtColor(f["observation/images/"+key][i],cv2.COLOR_RGB2BGR))[1].tobytes() for key in ("overview","wrist")]
        self.show_images(images)
        extra=(f" | 覆盖 {float(f['diagnostics/wipe_fraction'][i]):.1%} | 接触力 {float(f['diagnostics/wipe_force'][i]):.2f}N"
               if f.attrs.get("execution_unit")=="wipe" else "")
        self.live_text.set(f"预览 {Path(f.filename).name}  |  帧 {i+1}/{len(f['action'])}  |  {f['stage'].asstr()[i]}"+extra+"  |  点击任意控制返回实时画面")
        self.preview_index+=1
        if self.preview_index>=len(f["action"]):self.stop_preview();return
        self.preview_after=self.root.after(50,self.preview_tick)

    def stop_preview(self):
        if self.preview_after:self.root.after_cancel(self.preview_after);self.preview_after=None
        if self.preview:self.preview.close();self.preview=None

    def start_job(self,job):
        if self.mode!="idle" or self.job:
            messagebox.showinfo("当前忙碌","请先结束录制/批采，或等待当前检查完成")
            return
        if not self.rows:messagebox.showinfo("没有数据","请先录制轨迹");return
        self.stop_preview()
        self.job=job
        self.status.set("正在物理回放检查"+("并转换导出…" if job=="export" else "…"))
        session=self.session
        def background():
            logfile=session/(job+"_"+time.strftime("%Y%m%d_%H%M%S")+".log")
            try:
                with logfile.open("w",encoding="utf-8") as log:
                    process=subprocess.Popen([sys.executable,"-u","-m","workbench.jobs",job,str(session)],cwd=ROOT,
                        stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding="utf-8",errors="replace",
                        env={**__import__("os").environ,"PYTHONIOENCODING":"utf-8"})
                    for line in process.stdout:
                        log.write(line);log.flush();self.events.put(dict(kind="job_log",text=line.rstrip()))
                    code=process.wait()
                self.events.put(dict(kind="job_done",code=code,log=str(logfile)))
            except Exception as exc:self.events.put(dict(kind="job_done",code=1,log=str(logfile),error=str(exc)))
        threading.Thread(target=background,daemon=True).start()

    def poll(self):
        if self.closing:return
        while True:
            try:event=self.events.get_nowait()
            except queue.Empty:break
            kind=event["kind"]
            if kind=="ready":self.mode="idle";self.status.set("就绪：拖动/填写坐标后应用，或点击键盘控制区开始人工操作")
            elif kind=="reset":
                for key in ("cube_xy","goal_xy"):
                    for v,x in zip(self.values[key],event[key]):v.set(f"{x:.6f}")
                self.status.set(f"已应用 seed={event['seed']}，空间集合={event['split']}")
            elif kind=="mode":
                self.mode=event["value"]
                self.record_button.configure(text="保存录制  [Enter]" if self.mode=="recording" else "开始 / 保存录制  [Enter]")
                self.status.set({"idle":"就绪","recording":"正在录制人工示范；按1/2/3标阶段，Enter保存","batch":"自动批采进行中；记录真实随机起点与目标"}.get(self.mode,self.mode))
            elif kind=="saved":
                self.logline(f"已保存 {'成功' if event['success'] else '失败'} · {event['frames']}帧 · {Path(event['path']).name}")
                self.refresh()
            elif kind in ("message","error","job_log"):
                self.logline(event["text"])
                if kind=="error":self.status.set(event["text"])
            elif kind=="fatal":self.status.set("模拟器错误，请查看日志");self.logline(event["text"]);self.mode="error"
            elif kind=="job_done":
                self.job=None;self.refresh()
                self.status.set(("检查/导出完成。" if event["code"]==0 else "检查/导出失败，请查看日志；原始数据已保留。")+event["log"])
                self.logline(self.status.get())
        latest=None
        while True:
            try:latest=self.frames.get_nowait()
            except queue.Empty:break
        if latest:
            self.actual=latest
            if not self.preview:
                self.show_images(latest["images"])
                extra=f" | 擦拭覆盖 {latest.get('wipe_fraction',0):.1%} | 接触力 {latest.get('wipe_force',0):.2f}N" if self.task_code.get()=="wipe" else ""
                self.live_text.set(f"实时 | {latest['phase']} | t={latest['time']:.2f}s | 当前记录 {latest['frames']}帧 | TCP=["+", ".join(f"{x:.3f}" for x in latest['tcp'])+"] m"+extra)
            self.draw_map()
        if self.process and not self.process.is_alive() and self.mode not in ("error","starting"):
            self.mode="error";self.status.set("模拟器已退出，请重新打开场次；完整轨迹已保留")
        self.root.after(70,self.poll)

    def switch(self,path):
        if self.mode not in ("idle","error") or self.job:raise ValueError("请先保存/放弃录制，停止批采并等待任务完成")
        self.stop_preview()
        if self.process and self.process.is_alive():
            self.commands.put(dict(op="shutdown"));self.process.join(timeout=4)
            if self.process.is_alive():raise ValueError("模拟器仍在退出，请稍后重试")
        self.session=Path(path);r=json.loads((self.session/"recipe.json").read_text(encoding="utf-8"))
        self.set_recipe(r);self.actual=None;self.start_sim(r);self.refresh()

    def new(self):
        try:
            if self.mode not in ("idle","error") or self.job:raise ValueError("请先结束当前任务")
            self.switch(new_session(self.recipe()))
        except Exception as exc:messagebox.showerror("无法新建",str(exc))

    def open_session(self):
        if self.mode not in ("idle","error") or self.job:return
        from workbench.runtime import data_home
        path=filedialog.askdirectory(initialdir=data_home()/"workbench",title="选择含recipe.json的采集场次")
        if not path:return
        try:self.switch(path)
        except Exception as exc:messagebox.showerror("无法打开",str(exc))

    def import_task_package(self):
        try:
            if self.mode not in ("idle","error") or self.job:raise ValueError("请先结束采集或转换")
            source=filedialog.askopenfilename(title="选择任务包",filetypes=[("任务包","*.zip")])
            if not source:return
            from workbench.task_packages import inspect_package,install_package
            report=inspect_package(source);m=report["manifest"]
            enable=messagebox.askyesnocancel("检查完成 · 是否信任并启用",f"任务：{m['id']}  v{m['version']}\n作者：{m['author']}\n{m['description']}\n文件数：{len(m['files'])}\n包哈希：{report['manifest_sha256']}\n\n任务包含Python代码，请确认来源。\n是：安装并配置启用（重启生效）\n否：仅保存，暂不启用\n取消：不安装")
            if enable is None:return
            result=install_package(source,enable=enable)
            messagebox.showinfo("任务包已安装",result["path"]+("\n请关闭并重新启动工作台，新任务会出现在任务列表。" if enable else "\n尚未启用，可稍后再次导入并启用。"))
        except Exception as exc:messagebox.showerror("无法安装任务包",str(exc))

    def export_task_package(self):
        try:
            if self.mode not in ("idle","error") or self.job:raise ValueError("请先结束采集或转换")
            source=filedialog.askdirectory(title="选择含task_package.json的任务源目录")
            if not source:return
            output=filedialog.asksaveasfilename(title="导出任务包",defaultextension=".zip",filetypes=[("任务包","*.zip")])
            if not output:return
            from workbench.task_packages import export_package
            result=export_package(source,output)
            messagebox.showinfo("已导出任务包",result["path"]+"\nSHA256: "+result["sha256"])
        except Exception as exc:messagebox.showerror("无法导出",str(exc))

    def task_library_help(self):
        window=tk.Toplevel(self.root);window.title("共享任务库");window.geometry("850x650")
        text=tk.Text(window,wrap="word",font=("Microsoft YaHei UI",11));text.pack(fill="both",expand=True)
        text.insert("1.0",(ROOT/"TASK_SHARING_zh.md").read_text(encoding="utf-8"));text.configure(state="disabled")

    def help(self):
        window=tk.Toplevel(self.root);window.title("采集工作台操作说明");window.geometry("850x650")
        text=tk.Text(window,wrap="word",font=("Microsoft YaHei UI",11));text.pack(fill="both",expand=True)
        text.insert("1.0",(ROOT/"WORKBENCH_zh.md").read_text(encoding="utf-8"));text.configure(state="disabled")

    def close(self):
        if self.job:messagebox.showinfo("检查/转换进行中","请等待当前任务完成再关闭，避免留下不完整导出");return
        if self.mode in ("recording","batch") and not messagebox.askyesno("结束采集","关闭会放弃当前未完成轨迹，已保存的数据保留。继续关闭？"):return
        self.closing=True;self.stop_preview()
        if self.process and self.process.is_alive():
            self.commands.put(dict(op="shutdown"));self.process.join(timeout=4)
            if self.process.is_alive():self.process.terminate()
        self.root.destroy()


def main():
    mp.freeze_support()
    p=argparse.ArgumentParser()
    p.add_argument("--session",type=Path)
    p.add_argument("--ui-smoke-seconds",type=int,help="Open UI and close automatically for local QA")
    a=p.parse_args()
    root=desktop_root()
    app=Workbench(root,a.session)
    if a.ui_smoke_seconds:root.after(a.ui_smoke_seconds*1000,app.close)
    root.mainloop()


if __name__=="__main__":main()
