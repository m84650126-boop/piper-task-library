"""Local single-client HTTP chunk server. Requires a trained PiPER checkpoint.

Simulation adapter only. It is not an authorized real-robot controller.
"""
import argparse
import base64
import json
import hashlib
import sys
import importlib.metadata
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import numpy as np


def decode_rgb(encoded):
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) != 240*320*3:
        raise ValueError("expected uint8 RGB 240x320x3")
    return np.frombuffer(raw, dtype=np.uint8).reshape(240, 320, 3)


class PolicyBackend:
    def __init__(self, checkpoint, device):
        if importlib.metadata.version("lerobot")!="0.6.2":raise ValueError("This platform targets LeRobot0.6.2; select a matching inference environment")
        import torch
        from lerobot.configs import PreTrainedConfig
        from lerobot.policies.factory import get_policy_class, make_pre_post_processors
        self.torch = torch
        self.device = device
        checkpoint=Path(checkpoint).resolve()
        if device.startswith("cuda") and not torch.cuda.is_available():
            raise ValueError("CUDA is unavailable in this inference environment; choose CPU or a CUDA-enabled Python")
        self.config = PreTrainedConfig.from_pretrained(str(checkpoint))
        self.config.device = device
        if tuple(self.config.output_features["action"].shape) != (7,):
            raise ValueError("checkpoint must have seven PiPER action dimensions")
        expected={"observation.state":(7,),"observation.images.camera1":(3,240,320),"observation.images.camera2":(3,240,320)}
        actual={k:tuple(v.shape) for k,v in self.config.input_features.items()}
        if actual!=expected:raise ValueError("Checkpoint inputs do not match the PiPER state/camera1/camera2 mapping: "+str(actual))
        model_root = Path(__file__).resolve().parent.parent / "models"
        if self.config.type == "smolvla" and (model_root / "smolvlm").exists():
            self.config.vlm_model_name = str(model_root / "smolvlm")
        if self.config.type == "pi05" and (model_root / "paligemma").exists():
            self.config.text_tokenizer_name = str(model_root / "paligemma")
        self.policy = get_policy_class(self.config.type).from_pretrained(
            str(checkpoint), config=self.config, strict=True, local_files_only=True).eval()
        overrides = {"device_processor": {"device": device}}
        tokenizer_path = model_root / ("smolvlm" if self.config.type == "smolvla" else "paligemma")
        if (checkpoint / "tokenizer").is_dir():
            overrides["tokenizer_processor"] = {"tokenizer_name":str((checkpoint / "tokenizer").resolve())}
        elif tokenizer_path.exists():
            overrides["tokenizer_processor"] = {"tokenizer_name":str(tokenizer_path)}
        self.pre, self.post = make_pre_post_processors(self.config, pretrained_path=str(checkpoint),
            preprocessor_overrides=overrides)
        self.session = 0
        self.request = 0
        self.description = {"checkpoint": str(checkpoint.resolve()), "device": device,
            "policy_type": self.config.type, "config": json.loads((checkpoint / "config.json").read_text()),
            "preprocessor": json.loads((checkpoint / "policy_preprocessor.json").read_text()),
            "postprocessor": json.loads((checkpoint / "policy_postprocessor.json").read_text())}
        sha=hashlib.sha256()
        with (checkpoint/"model.safetensors").open("rb") as stream:
            for block in iter(lambda:stream.read(4*1024*1024),b""):sha.update(block)
        self.description["checkpoint_sha256"]=sha.hexdigest()
        self.description["torch_threads"]=torch.get_num_threads()
        self.description.update(torch=str(torch.__version__),cuda=torch.version.cuda,lerobot_version=importlib.metadata.version("lerobot"),
            python=sys.version,executable=sys.executable,device_name=torch.cuda.get_device_name() if device.startswith("cuda") else "CPU")
        self.description["execution_precision"]="CUDA bfloat16 autocast" if device.startswith("cuda") else "CPU float32 without autocast"
        self.description["input_convention"]="state float32 (1,7); RGB CHW float32 [0,1]; saved preprocessor and postprocessor"

    def reset(self, seed=None):
        if seed is not None:
            self.torch.manual_seed(int(seed))
            if self.torch.cuda.is_available():
                self.torch.cuda.manual_seed_all(int(seed))
        self.policy.reset()
        self.pre.reset()
        self.post.reset()
        self.session += 1
        self.request = 0
        return {"session": self.session}

    def predict(self, obj):
        torch = self.torch
        if obj["session"] != self.session or obj["request"] != self.request:
            raise ValueError("stale or out-of-order request")
        state = np.asarray(obj["state"], dtype=np.float32)
        if state.shape != (7,) or not np.isfinite(state).all():
            raise ValueError("invalid state")
        batch = {"observation.state": torch.from_numpy(state).unsqueeze(0), "task": [obj["task"]]}
        for name in ("camera1", "camera2"):
            # Exactly the loader's RGB CHW float [0,1] convention, before saved processors.
            image = decode_rgb(obj[name]).copy()
            batch[f"observation.images.{name}"] = torch.from_numpy(image).permute(2, 0, 1).float().div(255).unsqueeze(0)
        if self.device.startswith("cuda"):
            torch.cuda.synchronize()
            torch.cuda.reset_peak_memory_stats()
        start = time.perf_counter()
        with torch.inference_mode(), torch.autocast(device_type="cuda", dtype=torch.bfloat16, enabled=self.device.startswith("cuda")):
            prepared = self.pre(batch)
            prediction = self.policy.predict_action_chunk(prepared)
            action = self.post(prediction).float().cpu().numpy()[0]
        if self.device.startswith("cuda"):
            torch.cuda.synchronize()
        if action.ndim != 2 or action.shape[1] != 7 or not np.isfinite(action).all():
            raise ValueError("invalid predicted action")
        self.request += 1
        return dict(session=self.session, request=obj["request"], actions=action.tolist(),
            inference_s=time.perf_counter()-start,
            peak_allocated_bytes=torch.cuda.max_memory_allocated() if self.device.startswith("cuda") else None)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--checkpoint", type=Path, required=True, help="checkpoints/STEP/pretrained_model")
    p.add_argument("--device", default="cuda")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--host",default="127.0.0.1")
    p.add_argument("--record", type=Path, default=Path("runs/inference_service.json"))
    a = p.parse_args()
    backend = PolicyBackend(a.checkpoint, a.device)
    a.record.parent.mkdir(parents=True, exist_ok=True)
    a.record.write_text(json.dumps(backend.description, indent=2))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path!="/health":self.send_error(404);return
            body=json.dumps(dict(status="ready",api_version=1,description=backend.description)).encode()
            self.send_response(200);self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body)

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 2_000_000:
                    raise ValueError("invalid request length")
                obj = json.loads(self.rfile.read(length))
                if self.path == "/reset":
                    result = backend.reset(obj.get("policy_noise_seed"))
                elif self.path == "/predict":
                    result = backend.predict(obj)
                else:
                    raise ValueError("unknown endpoint")
                body = json.dumps(result).encode()
                self.send_response(200)
            except Exception as e:
                body = json.dumps({"error": str(e)}).encode()
                self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    print(f"PiPER simulation inference on {a.host}:{a.port}; one client", flush=True)
    HTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
