"""Separate model environment, offline local service or user-selected HTTP endpoint."""
import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.error
import urllib.request
from .runtime import ROOT,conversion_python,cache_home


class InterruptedEvaluation(Exception):pass


def request(base,path,obj=None,timeout=1.):
    data=json.dumps(obj).encode() if obj is not None else None
    req=urllib.request.Request(base.rstrip("/")+path,data=data,headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=timeout) as response:
        payload=response.read(8*1024*1024+1)
    if len(payload)>8*1024*1024:raise ValueError("Oversized policy response")
    return json.loads(payload)


class PolicyClient:
    def __init__(self,spec,folder,stopped,emit):
        self.spec=spec;self.folder=Path(folder);self.stopped=stopped;self.emit=emit
        self.process=None;self.log=None;self.description=None

    def start(self):
        s=self.spec
        if s["backend"]=="local":
            python=Path(s.get("inference_python") or conversion_python()).expanduser().resolve()
            if not python.is_file():raise ValueError("推理Python不存在，请选择安装了LeRobot模型依赖的Python")
            with socket.socket() as sock:sock.bind(("127.0.0.1",0));port=sock.getsockname()[1]
            self.base=f"http://127.0.0.1:{port}"
            self.log=(self.folder/"model_service.log").open("w",encoding="utf-8")
            env={**os.environ,"PYTHONIOENCODING":"utf-8","HF_HOME":str(cache_home()),"HF_HUB_OFFLINE":"1","TRANSFORMERS_OFFLINE":"1","OMP_NUM_THREADS":"4"}
            self.process=subprocess.Popen([str(python),"-u",str(ROOT/"a100/inference_service.py"),"--checkpoint",s["checkpoint"],
                "--device",s["device"],"--port",str(port),"--record",str(self.folder/"model_description.json")],cwd=ROOT,env=env,
                stdout=self.log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
        else:self.base=s["service"].rstrip("/")
        end=time.monotonic()+float(s["load_timeout_s"])
        self.emit("status",text="正在加载模型／等待推理服务；仿真暂未推进")
        while True:
            if self.stopped():raise InterruptedEvaluation()
            if self.process and self.process.poll() is not None:raise RuntimeError("模型服务启动失败，请查看本次评测目录的model_service.log")
            try:
                health=request(self.base,"/health",timeout=.7)
                break
            except urllib.error.HTTPError as exc:
                raise RuntimeError("服务缺少兼容的/health接口，请使用本平台a100/inference_service.py") from exc
            except (OSError,TimeoutError):
                if s["backend"]=="http" or time.monotonic()>=end:raise TimeoutError("推理服务未就绪；检查地址、依赖和model_service.log")
                time.sleep(.2)
        if health.get("api_version")!=1 or health.get("status")!="ready":raise ValueError("Unsupported policy service protocol")
        self.description=health["description"]
        c=self.description["config"]
        expected={"observation.state":[7],"observation.images.camera1":[3,240,320],"observation.images.camera2":[3,240,320]}
        if {k:v["shape"] for k,v in c["input_features"].items()}!=expected or c["output_features"]["action"]["shape"]!=[7]:raise ValueError("远程模型输入/动作与本平台不兼容")
        if s["prefix"]>int(c["chunk_size"]):raise ValueError("执行前缀超过模型动作块长度")
        (self.folder/"model_description.json").write_text(json.dumps(self.description,indent=2),encoding="utf-8")
        self.emit("model_ready",description={k:self.description.get(k) for k in ("policy_type","device","checkpoint_sha256")})

    def reset(self,seed):
        self.session=request(self.base,"/reset",dict(policy_noise_seed=seed),float(self.spec["request_timeout_s"]))["session"]
        self.request_id=0

    def predict(self,obs,text):
        obj=dict(session=self.session,request=self.request_id,state=obs["state"].tolist(),task=text)
        for raw,key in (("overview","camera1"),("wrist","camera2")):obj[key]=base64.b64encode(obs[raw].tobytes()).decode()
        begin=time.perf_counter()
        result=request(self.base,"/predict",obj,float(self.spec["request_timeout_s"]))
        elapsed=time.perf_counter()-begin
        if self.stopped():raise InterruptedEvaluation()
        if result.get("session")!=self.session or result.get("request")!=self.request_id:raise ValueError("Stale/out-of-order inference response rejected")
        self.request_id+=1
        return result,elapsed

    def close(self):
        if self.process:
            self.process.terminate()
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
        if self.log:self.log.close()
