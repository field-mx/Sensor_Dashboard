"""Freeze baseline residual thresholds from a separate normal calibration interval."""
from pathlib import Path
import sys,json,hashlib
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from sensor_demo.data import read_excel
from sensor_demo.config import ROOT,DEFAULT_DATA
from demo_server.model import ModelService,BASELINE
from demo_server.calibration import calibrate_thresholds

def main():
    data=read_excel(DEFAULT_DATA);service=ModelService();assert service.version==BASELINE
    start,end=14540,19387
    cutoffs=np.linspace(start+95,end-2,128,dtype=int).tolist()
    floors=service.predictor.scale.detach().cpu().numpy().reshape(-1)*.05
    result=calibrate_thresholds(service.predictor,data.model_values,cutoffs,data.model_channels,floors)
    result.update({'model_version':BASELINE,'source_sha256':data.file_sha256,'calibration_partition':[start,end],'cutoff_seqs':cutoffs,'channels':data.model_channels,'method':'max(normal absolute one-step residual q99, training raw_scale * 0.05)','assumption':'Existing workbook treated as normal for functional demo; not a labeled fault-performance evaluation.'})
    result['calibration_id']=hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()
    target=ROOT/'configs/baseline-alarm.json';target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'samples':128,'calibration_id':result['calibration_id'],'TT010301_threshold':result['thresholds'][data.model_channels.index('TT010301')]},indent=2))

if __name__=='__main__':main()
