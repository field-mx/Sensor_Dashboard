from __future__ import annotations
import sys,threading,time,json,hashlib
from datetime import timedelta
import numpy as np
from sensor_demo.config import ROOT
from .contracts import CHANNELS,SCHEMA_ID

MODEL_ROOT=ROOT/'vendor/PhysicsRelationCTF'
BASELINE='prctf-seed42-epoch12'

class ModelService:
    def __init__(self):
        self.lock=threading.RLock();self.version=BASELINE;self.busy=False
        self.bundle_path=MODEL_ROOT/'weights/industrial/prctf_seed42.pt';self.bundle_sha256=hashlib.sha256(self.bundle_path.read_bytes()).hexdigest()
        calibration=ROOT/'configs/baseline-alarm.json';self.calibration=json.loads(calibration.read_text(encoding='utf-8')) if calibration.exists() else None
        import torch
        torch.set_num_threads(2)
        sys.path.insert(0,str(MODEL_ROOT))
        from scripts.pretrained import load_pretrained
        self.predictor=load_pretrained('industrial',device='cpu')
        if self.predictor.channels!=CHANNELS:raise ValueError('公开模型schema与Demo不一致')
    def spec(self):
        with self.lock:
            result={'model_version':self.version,'schema_id':SCHEMA_ID,'input_channel_ids':CHANNELS,'output_channel_ids':CHANNELS,'sample_period_ms':1000,'input_length':96,'prediction_length':96,'forecast_offsets':list(range(1,97)),'task_kind':'future_forecasting','preprocessing_id':'raw-workbook-units-no-lowpass','supports_mask':False,'trainable':True,'device':'cpu','recipe_ids':['prctf_demo_finetune_v1']}
            if self.calibration:result['alarm_calibration']=self.calibration
            return result
    def predict(self,request):
        start=time.perf_counter()
        with self.lock:
            if self.busy:raise RuntimeError('训练中，预测暂停')
            if request.model_version!=self.version:raise ValueError('活动模型版本已改变，请重新获取模型信息')
            result=self.predictor.predict(np.asarray(request.history),channels=request.channel_ids)
            version=self.version
        cutoff=request.source_times[-1]
        return {'run_id':request.run_id,'generation':request.generation,'request_id':request.request_id,'model_version':version,'schema_id':SCHEMA_ID,'cutoff_seq':request.cutoff_seq,'forecast_offsets':list(range(1,97)),'target_seqs':list(range(request.cutoff_seq+1,request.cutoff_seq+97)),'target_times':[(cutoff+timedelta(seconds=i)).isoformat() for i in range(1,97)],'output_channel_ids':CHANNELS,'predictions':result.tolist(),'latency_ms':round((time.perf_counter()-start)*1000,2)}
