"""Live evaluation dashboard; independent subprocesses keep collection UI responsive."""
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
from .runtime import ROOT,data_home,conversion_python
from .eval_protocol import validate_spec,freeze_conditions
from .tasks import TASKS


class EvaluationPanel:
    def __init__(self,app):
        self.app=app;self.recipe=app.recipe();self.process=None;self.running=False;self.folder=None;self.rows={};self.photos=[]
        self.events=queue.Queue();self.frames=queue.Queue(maxsize=1);self.run_id=0;self.preview=None;self.preview_after=None;self.inference_begin=None;self.model_load_begin=None;self.stop_requested=False
        self.window=tk.Toplevel(app.root);self.window.title("仿真闭环评测 · 模型执行与实时观测");self.window.geometry("1030x800")
        self.window.minsize(850,650);self.window.protocol("WM_DELETE_WINDOW",self.close)
        self.status=tk.StringVar(value="请选择后端并检查设置。模型闭环当前支持完整拾放及其配置变体。")
        self.metrics=tk.StringVar(value="完成0条 | 成功率— | 推理p50/p95：—")
        self.state=tk.StringVar(value="等待仿真画面")
        ttk.Label(self.window,text="同步闭环：推理时仿真暂停；实时观测不等于真实机器人实时控制验证。",padding=8).pack(fill="x")
        self.book=ttk.Notebook(self.window);self.book.pack(fill="both",expand=True,padx=10,pady=5)
        setup=ttk.Frame(self.book,padding=12);live=ttk.Frame(self.book,padding=10);results=ttk.Frame(self.book,padding=10)
        self.book.add(setup,text="1 · 模型与评测设置");self.book.add(live,text="2 · 实时观测");self.book.add(results,text="3 · 逐次结果与回放")
        self.backends={"本地模型 · SmolVLA / π₀.₅":"local","HTTP推理服务 · 本机或训练工作站":"http","专家控制器自检 · 不是模型成绩":"expert"}
        defaults=dict(backend=next(iter(self.backends)),checkpoint=str(ROOT/"pretrained_model"),inference_python=str(conversion_python()),device="cpu",
            service="http://127.0.0.1:8765",trials="1",max_steps="400",prefix="10",request_timeout_s="60",load_timeout_s="300",policy_seed="101",cohort="custom_unclassified",conditions_file="")
        self.values={key:tk.StringVar(value=value) for key,value in defaults.items()};self.pace=tk.BooleanVar(value=True)
        def entry(row,key,title,browse=None):
            ttk.Label(setup,text=title).grid(row=row,column=0,sticky="w",pady=5)
            ttk.Entry(setup,textvariable=self.values[key],width=65).grid(row=row,column=1,sticky="ew",padx=5)
            if browse:ttk.Button(setup,text="选择…",command=lambda:self.choose(key,browse)).grid(row=row,column=2)
        ttk.Label(setup,text="评测后端").grid(row=0,column=0,sticky="w")
        ttk.Combobox(setup,textvariable=self.values["backend"],values=list(self.backends),state="readonly",width=60).grid(row=0,column=1,sticky="ew",padx=5,pady=5)
        entry(1,"checkpoint","模型目录", "directory");entry(2,"inference_python","模型推理Python","python")
        entry(3,"device","设备：cpu / cuda / cuda:0");entry(4,"service","HTTP服务地址（仅HTTP后端）")
        controls=ttk.Frame(setup);controls.grid(row=5,column=0,columnspan=3,sticky="ew",pady=8)
        for index,(key,label) in enumerate((("trials","试验数"),("max_steps","每条最大步数"),("prefix","动作执行前缀"))):
            ttk.Label(controls,text=label).grid(row=0,column=2*index,padx=4);ttk.Entry(controls,textvariable=self.values[key],width=9).grid(row=0,column=2*index+1)
        for index,(key,label) in enumerate((("request_timeout_s","请求超时/秒"),("load_timeout_s","加载超时/秒"),("policy_seed","策略噪声seed"))):
            ttk.Label(controls,text=label).grid(row=1,column=2*index,padx=4,pady=8);ttk.Entry(controls,textvariable=self.values[key],width=9).grid(row=1,column=2*index+1)
        entry(6,"cohort","条件组标签（不会自动判定泛化）");entry(7,"conditions_file","固定测试条件JSON（可留空）","json")
        self.recipe_text=tk.StringVar();self.update_recipe()
        ttk.Label(setup,textvariable=self.recipe_text,wraplength=760,justify="left").grid(row=8,column=0,columnspan=3,sticky="w",pady=8)
        ttk.Button(setup,text="从采集主窗口更新任务 / 场景 / 范围",command=self.update_recipe).grid(row=9,column=0,columnspan=2,sticky="w")
        ttk.Checkbutton(setup,text="按20Hz播放动作，便于观察（推理等待另计）",variable=self.pace).grid(row=10,column=0,columnspan=3,sticky="w",pady=8)
        ttk.Label(setup,text="本地模型使用独立LeRobot推理环境；CPU可能很慢。HTTP后端可连接4090/A100工作站。\n专家自检只验证平台与任务链路，不产生模型成功率。",wraplength=760).grid(row=11,column=0,columnspan=3,sticky="w")
        setup.columnconfigure(1,weight=1)
        images=ttk.Frame(live);images.pack()
        self.image_labels=[]
        for i,name in enumerate(("固定相机 overview","腕部相机 wrist")):
            ttk.Label(images,text=name).grid(row=0,column=i,padx=10)
            label=ttk.Label(images,text="等待画面",anchor="center",width=44);label.grid(row=1,column=i,padx=8,pady=8);self.image_labels.append(label)
        ttk.Label(live,textvariable=self.state,wraplength=900,justify="left").pack(fill="x",pady=8)
        self.log=tk.Text(live,height=8,wrap="word");self.log.pack(fill="both",expand=True)
        self.table=ttk.Treeview(results,columns=("trial","seed","result","sim","wall","p95","reason"),show="headings",height=12)
        for key,label in (("trial","试验"),("seed","场景seed"),("result","结果"),("sim","仿真秒"),("wall","墙钟秒"),("p95","推理p95/秒"),("reason","终止原因")):
            self.table.heading(key,text=label);self.table.column(key,width=90 if key!="reason" else 220,stretch=True)
        self.table.pack(fill="both",expand=True)
        tools=ttk.Frame(results);tools.pack(fill="x",pady=8)
        ttk.Button(tools,text="回放选中轨迹",command=self.replay).pack(side="left",padx=4)
        ttk.Button(tools,text="打开历史评测目录…",command=self.open_history).pack(side="left",padx=4)
        ttk.Button(tools,text="打开本次结果目录",command=self.open_folder).pack(side="left",padx=4)
        bottom=ttk.Frame(self.window,padding=10);bottom.pack(fill="x")
        self.start_button=ttk.Button(bottom,text="开始闭环评测",command=self.start);self.start_button.pack(side="left")
        self.stop_button=ttk.Button(bottom,text="停止并清队列",command=self.stop,state="disabled");self.stop_button.pack(side="left",padx=6)
        self.force_button=ttk.Button(bottom,text="强制终止本次进程",command=self.force_stop,state="disabled");self.force_button.pack(side="left")
        ttk.Button(bottom,text="评测操作说明",command=self.help).pack(side="right")
        ttk.Label(self.window,textvariable=self.metrics,padding=(10,2)).pack(fill="x")
        ttk.Label(self.window,textvariable=self.status,padding=(10,4),wraplength=960).pack(fill="x")
        self.window.after(100,self.poll)

    def choose(self,key,kind):
        path=filedialog.askdirectory(parent=self.window) if kind=="directory" else filedialog.askopenfilename(parent=self.window,filetypes=[("JSON","*.json")] if kind=="json" else [("Python","python.exe"),("全部","*")])
        if path:self.values[key].set(path)

    def update_recipe(self):
        try:self.recipe=self.app.recipe()
        except (ValueError,TypeError) as exc:
            messagebox.showerror("场景设置无效",str(exc),parent=self.window);return
        r=self.recipe
        if hasattr(self,"recipe_text"):self.recipe_text.set(f"任务：{TASKS[r['task']]['label']} | 场景：{r['environment']} | 物品：{r['object']} | 采样：{r['mode']}\n起点：{r['cube_xy']}；目标：{r['goal_xy']}；seed={r['seed']}\n起点范围：{r['cube_bounds']}；目标范围：{r['goal_bounds']}。开始后冻结本次条件。")

    def specification(self):
        s={key:value.get() for key,value in self.values.items() if key!="backend"}
        s["backend"]=self.backends[self.values["backend"].get()]
        for key in ("trials","max_steps","prefix","policy_seed"):s[key]=int(s[key])
        for key in ("request_timeout_s","load_timeout_s"):s[key]=float(s[key])
        return validate_spec({**s,"recipe":self.recipe,"pace":self.pace.get()})

    def start(self):
        if self.running:return
        try:
            s=self.specification();freeze_conditions(s)
            self.stop_preview();self.folder=data_home()/"evaluations"/datetime.now().strftime("run_%Y%m%d_%H%M%S_%f");self.folder.mkdir(parents=True)
            config=self.folder/"config.json";config.write_text(json.dumps(s,indent=2),encoding="utf-8")
            self.rows={};self.table.delete(*self.table.get_children());self.run_id+=1;run_id=self.run_id
            self.inference_begin=None;self.model_load_begin=None;self.stop_requested=False;self.status.set("正在启动独立评测进程…");self.metrics.set("完成0条 | 成功率—")
            self.process=subprocess.Popen([sys.executable,"-u","-m","workbench.eval_runner","--config",str(config)],cwd=ROOT,
                stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding="utf-8",errors="replace",env={**os.environ,"PYTHONIOENCODING":"utf-8"},
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0,start_new_session=os.name!="nt")
            self.running=True;self.start_button.configure(state="disabled");self.stop_button.configure(state="normal");self.force_button.configure(state="normal")
            self.book.select(1);process=self.process;folder=self.folder
            def read():
                with (folder/"runner.log").open("w",encoding="utf-8") as log:
                    for line in process.stdout:
                        if line.startswith("WB_EVENT "):
                            try:event=json.loads(line[9:])
                            except ValueError:event=dict(kind="status",text=line.rstrip())
                            event["run_id"]=run_id
                            if event["kind"]=="frame":
                                try:self.frames.get_nowait()
                                except queue.Empty:pass
                                self.frames.put_nowait(event)
                            else:self.events.put(event);log.write(line);log.flush()
                        else:log.write(line);log.flush();self.events.put(dict(kind="log",text=line.rstrip(),run_id=run_id))
                self.events.put(dict(kind="process_exit",code=process.wait(),run_id=run_id))
            threading.Thread(target=read,daemon=True).start()
        except Exception as exc:messagebox.showerror("无法启动评测",str(exc),parent=self.window)

    def logline(self,text):
        self.log.insert("end",text+"\n");self.log.see("end")
        if int(self.log.index("end-1c").split(".")[0])>300:self.log.delete("1.0","100.0")

    def display_summary(self,s):
        rate=s.get("completion_rate");lat=s.get("inference",{});fmt=lambda x:"—" if x is None else f"{x:.3f}"
        self.metrics.set(f"{'专家自检（非模型） | ' if s.get('expert_is_not_learned_policy') else ''}完成{s.get('completed_trials',0)}/{s.get('planned_trials',0)} | 成功{s.get('successes',0)} | 成功率{'—' if rate is None else format(rate,'.1%')} | 推理p50/p95：{fmt(lat.get('p50_s'))}/{fmt(lat.get('p95_s'))}秒 | 超时{s.get('counters',{}).get('timeouts',0)}")

    def add_trial(self,r):
        key=str(r["trial"]);self.rows[key]=r
        if self.table.exists(key):self.table.delete(key)
        self.table.insert("","end",iid=key,values=(r["trial"],r["seed"],"中断" if r.get("interrupted") else "成功" if r["success"] else "失败",f"{r['task_sim_s']:.2f}",f"{r['task_wall_s']:.2f}",r.get("inference",{}).get("p95_s"),r["termination"]))

    def poll(self):
        if not self.window.winfo_exists():return
        while True:
            try:event=self.events.get_nowait()
            except queue.Empty:break
            if event.get("run_id")!=self.run_id:continue
            kind=event["kind"]
            if kind in ("status","error","log"):
                self.status.set(event["text"]);self.logline(event["text"])
                if "正在加载模型" in event["text"]:self.model_load_begin=time.monotonic()
            elif kind=="model_ready":self.model_load_begin=None;self.logline("模型就绪："+json.dumps(event["description"],ensure_ascii=False))
            elif kind=="trial_start":self.status.set("评测试验 "+str(event["condition"]["trial"])+"；条件已固定")
            elif kind=="inference_start":self.inference_begin=time.monotonic()
            elif kind=="inference_done":
                self.inference_begin=None;self.status.set(f"推理{event['inference_s']:.3f}s，端到端{event['roundtrip_s']:.3f}s；动作块{event['action_chunk_length']}，执行前缀{event['prefix']}")
            elif kind=="trial_done":self.add_trial(event["result"]);self.display_summary(event["summary"])
            elif kind=="finished":self.display_summary(event["summary"]);self.status.set("评测结束："+event["summary"]["status"]+"；结果："+str(self.folder));self.inference_begin=None;self.model_load_begin=None
            elif kind=="process_exit":
                self.running=False;self.inference_begin=None;self.model_load_begin=None;self.start_button.configure(state="normal");self.stop_button.configure(state="disabled");self.force_button.configure(state="disabled")
                if event["code"] and not (self.folder/"forced_stop.json").exists():self.status.set("评测进程异常退出，请查看本次目录中的runner.log / error.log / model_service.log")
        latest=None
        while True:
            try:latest=self.frames.get_nowait()
            except queue.Empty:break
        if latest and latest.get("run_id")==self.run_id and not self.preview:
            self.photos=[tk.PhotoImage(data=x) for x in latest["images"]]
            for label,photo in zip(self.image_labels,self.photos):label.configure(image=photo,width=320)
            self.state.set(f"试验{latest['trial']} | 步{latest['step']} | 仿真{latest['sim_s']:.2f}s | 阶段{latest['phase']}\n物体XYZ={np_round(latest['cube_xyz'])}；TCP={np_round(latest['tcp_xyz'])}\n关节rad＋夹爪m：{np_round(latest['state'])}")
        if self.inference_begin:self.status.set(("已请求停止，在途响应不会执行；" if self.stop_requested else "模型推理中，")+f"已等待{time.monotonic()-self.inference_begin:.1f}秒；仿真暂停")
        elif self.model_load_begin:self.status.set(("正在停止加载；" if self.stop_requested else "正在加载模型／等待服务；")+f"已等待{time.monotonic()-self.model_load_begin:.1f}秒；界面可操作，详见model_service.log")
        self.window.after(100,self.poll)

    def stop(self):
        if self.running:
            self.stop_requested=True
            (self.folder/"STOP_REQUESTED").write_text("user requested interruption",encoding="utf-8")
            self.status.set("已请求停止；在途响应不会执行，等待当前请求结束并保存记录。必要时可强制终止。")

    def force_stop(self):
        if not self.running or self.process.poll() is not None:return
        self.stop()
        if os.name=="nt":subprocess.run(["taskkill","/PID",str(self.process.pid),"/T","/F"],capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
        else:os.killpg(self.process.pid,signal.SIGTERM)
        (self.folder/"forced_stop.json").write_text(json.dumps(dict(status="forced_interruption",uncommitted_trial_not_counted=True,partial_hdf5_may_remain=True),indent=2),encoding="utf-8")
        path=self.folder/"summary.json"
        if path.exists():
            s=json.loads(path.read_text(encoding="utf-8"));s["status"]="forced_interruption";s["uncommitted_trial_not_counted"]=True;path.write_text(json.dumps(s,indent=2),encoding="utf-8")
        self.status.set("已强制终止本次进程树；此前完成的记录保留，未提交轨迹不计入成功率。")

    def open_history(self):
        if self.running:return
        path=filedialog.askdirectory(initialdir=data_home()/"evaluations",parent=self.window)
        if not path:return
        try:
            self.stop_preview();self.folder=Path(path);s=json.loads((self.folder/"summary.json").read_text(encoding="utf-8"))
            self.rows={};self.table.delete(*self.table.get_children())
            for line in (self.folder/"trials.jsonl").read_text(encoding="utf-8").splitlines():self.add_trial(json.loads(line))
            self.display_summary(s);self.book.select(2)
        except Exception as exc:messagebox.showerror("无法打开报告",str(exc),parent=self.window)

    def open_folder(self):
        if not self.folder:return
        if os.name=="nt":os.startfile(self.folder)
        else:subprocess.Popen(["xdg-open",str(self.folder)])

    def replay(self):
        if self.running:return
        selected=self.table.selection()
        if not selected:return
        path=self.rows[selected[0]].get("raw_file")
        if not path:messagebox.showinfo("无轨迹","该试验在执行动作前终止，只有诊断报告。",parent=self.window);return
        try:
            import h5py
            path=Path(path)
            if not path.is_absolute():path=self.folder/path
            self.stop_preview();self.preview=h5py.File(path,"r");self.preview_index=0;self.book.select(1);self.preview_tick()
        except Exception as exc:messagebox.showerror("无法回放",str(exc),parent=self.window)

    def preview_tick(self):
        import cv2,base64
        if not self.preview:return
        i=self.preview_index
        self.photos=[tk.PhotoImage(data=base64.b64encode(cv2.imencode(".png",cv2.cvtColor(self.preview["observation/images/"+key][i],cv2.COLOR_RGB2BGR))[1])) for key in ("overview","wrist")]
        for label,photo in zip(self.image_labels,self.photos):label.configure(image=photo,width=320)
        self.state.set(f"历史记录回放 | 帧{i+1}/{len(self.preview['action'])}；不是当前在线执行")
        self.preview_index+=1
        if self.preview_index>=len(self.preview["action"]):self.stop_preview()
        else:self.preview_after=self.window.after(50,self.preview_tick)

    def stop_preview(self):
        if self.preview_after:self.window.after_cancel(self.preview_after);self.preview_after=None
        if self.preview:self.preview.close();self.preview=None

    def help(self):
        window=tk.Toplevel(self.window);window.title("仿真评测操作说明");window.geometry("850x650")
        text=tk.Text(window,wrap="word");text.pack(fill="both",expand=True);text.insert("1.0",(ROOT/"EVALUATION_zh.md").read_text(encoding="utf-8"));text.configure(state="disabled")

    def close(self):
        if self.running:self.stop();return
        self.stop_preview();self.window.destroy()


def np_round(values):return ", ".join(f"{x:.3f}" for x in values)
