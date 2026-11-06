"""Actual rendered expert evaluation, frozen conditions, task evidence and queue rejection."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from workbench.core import ROOT,DEFAULT
from workbench.eval_protocol import validate_spec,freeze_conditions,summary


class LiveEvaluation(unittest.TestCase):
    def test_conditions_are_frozen_and_explicit_positions_preserved(self):
        s=validate_spec(dict(backend="expert",trials=3,recipe={**DEFAULT,"mode":"random"}))
        self.assertEqual(freeze_conditions(s),freeze_conditions(s))
        with tempfile.TemporaryDirectory(dir=ROOT/"data") as tmp:
            path=Path(tmp)/"conditions.json"
            path.write_text(json.dumps(dict(conditions=[dict(seed=7,start_xy=[.39,-.09],goal_xy=[.37,.12])])),encoding="utf-8")
            fixed=freeze_conditions({**s,"trials":1,"conditions_file":str(path)})
            self.assertEqual(fixed[0]["goal_xy"],[.37,.12])

    def test_summary_excludes_interruption_and_uses_all_latency_samples(self):
        one=dict(success=True,task_sim_s=4.,task_wall_s=8.,inference_samples_s=[1.,3.],roundtrip_samples_s=[2.,4.])
        two=dict(success=False,interrupted=True,task_sim_s=0.,task_wall_s=2.,inference_samples_s=[2.],interventions=1)
        report=summary([one,two],5,"local","interrupted")
        self.assertEqual(report["completion_rate"],1.)
        self.assertEqual(report["completed_trials"],1)
        self.assertEqual(report["interrupted_trials"],1)
        self.assertEqual(report["inference"]["p50_s"],2.)
        self.assertFalse(report["real_time_deadline_validation"])

    def test_annotations_follow_actual_goal_and_reject_stale_unsafe_chunks(self):
        from a100.closed_loop import evaluation_phase
        from a100.execution_manager import ExecutionManager
        sample=dict(finger_contacts=np.ones(2,bool),cube_xyz=np.array([.41,.10,.18]),cube_in_tcp=np.zeros(3),cube_rotation_in_tcp=np.eye(3))
        samples=[sample]*10
        self.assertEqual(evaluation_phase("move_held_object",samples,[.41,.1]),"place")
        self.assertEqual(evaluation_phase("move_held_object",samples,[.2,-.2]),"move_held_object")
        manager=ExecutionManager(np.array([[-1.,1.]]*6),prefix=1);generation=manager.reset()
        manager.accept(np.array([[0.,0,0,0,0,0,.09]]),generation,.01);manager.stop()
        self.assertFalse(manager.queue)
        with self.assertRaises(RuntimeError):manager.accept(np.zeros((1,7)),generation,.01)
        generation=manager.reset()
        with self.assertRaises(ValueError):manager.accept(np.array([[2.,0,0,0,0,0,.09]]),generation,.01)
        self.assertEqual(manager.counters["constraint_violations"],1)

    def test_actual_rgb_expert_evaluation_records_success_and_physical_replay(self):
        from workbench.eval_runner import run
        from validate import validate_episode
        events=[]
        with tempfile.TemporaryDirectory(dir=ROOT/"data") as tmp:
            with patch("workbench.eval_runner.emit",side_effect=lambda kind,**data:events.append((kind,data))):
                result=run(dict(backend="expert",trials=1,max_steps=400,pace=False,recipe={**DEFAULT,"mode":"fixed","cube_xy":[.395,-.09],"goal_xy":[.37,.12]}),tmp)
            self.assertEqual(result["status"],"completed",result)
            self.assertEqual(result["successes"],1,result)
            self.assertTrue(result["expert_is_not_learned_policy"])
            trial=json.loads((Path(tmp)/"trials.jsonl").read_text(encoding="utf-8"))
            raw=Path(trial["raw_file"])
            self.assertTrue(validate_episode(raw if raw.is_absolute() else Path(tmp)/raw)["replay"][0]["passed"])
            self.assertTrue(any(kind=="frame" for kind,_ in events))
            self.assertIsNone(result["inference"]["p50_s"])

    def test_http_response_arriving_after_stop_never_executes_actions(self):
        import threading
        from http.server import BaseHTTPRequestHandler,HTTPServer
        from workbench.eval_runner import run
        with tempfile.TemporaryDirectory(dir=ROOT/"data") as tmp:
            folder=Path(tmp)
            class Handler(BaseHTTPRequestHandler):
                def log_message(self,*args):pass
                def answer(self,obj):
                    body=json.dumps(obj).encode();self.send_response(200);self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body)
                def do_GET(self):
                    features={k:dict(shape=v) for k,v in {"observation.state":[7],"observation.images.camera1":[3,240,320],"observation.images.camera2":[3,240,320]}.items()}
                    self.answer(dict(api_version=1,status="ready",description=dict(policy_type="synthetic_stop_test_not_a_model",config=dict(input_features=features,output_features=dict(action=dict(shape=[7])),chunk_size=50))))
                def do_POST(self):
                    obj=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    if self.path=="/reset":self.answer(dict(session=1));return
                    (folder/"STOP_REQUESTED").write_text("stop while inference response is in flight",encoding="utf-8")
                    self.answer(dict(session=1,request=obj["request"],actions=[obj["state"]]*50,inference_s=.01))
            server=HTTPServer(("127.0.0.1",0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                with patch("workbench.eval_runner.emit"):
                    result=run(dict(backend="http",service=f"http://127.0.0.1:{server.server_port}",trials=1,pace=False,recipe={**DEFAULT,"mode":"fixed"}),folder)
                self.assertEqual(result["status"],"interrupted",result)
                trial=json.loads((folder/"trials.jsonl").read_text(encoding="utf-8"))
                self.assertEqual(trial["steps"],0)
                self.assertIsNone(trial["raw_file"])
                self.assertEqual(trial["interventions"],1)
                self.assertEqual(result["completed_trials"],0)
            finally:server.shutdown();server.server_close();thread.join(timeout=2)


if __name__=="__main__":unittest.main()
