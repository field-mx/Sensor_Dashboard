"""Real Excel / pretrained CPU / HTTP consistency check. Never trains."""
from pathlib import Path
import sys,json,time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import httpx
from sensor_demo.config import ROOT,DEFAULT_DATA
from sensor_demo.data import read_excel
from demo_server.model import ModelService
from demo_server.contracts import PredictRequest

def main():
    dataset=read_excel(DEFAULT_DATA)
    model=ModelService()
    client=httpx.Client(base_url='http://127.0.0.1:18000',timeout=30)
    spec=client.get('/api/v1/model-spec').raise_for_status().json()
    latencies=[]
    for i in range(100):
        cutoff=95+i
        payload={'run_id':'real-verification','generation':1,'request_id':f'verify-{i}','model_version':spec['model_version'],'schema_id':spec['schema_id'],'cutoff_seq':cutoff,'source_times':[x.isoformat() for x in dataset.times[cutoff-95:cutoff+1]],'channel_ids':dataset.model_channels,'history':dataset.model_values[cutoff-95:cutoff+1].tolist()}
        start=time.perf_counter();response=client.post('/api/v1/predict',json=payload).raise_for_status().json();latencies.append((time.perf_counter()-start)*1000)
        values=np.asarray(response['predictions'])
        assert values.shape==(96,56) and np.isfinite(values).all()
        assert response['target_seqs']==list(range(cutoff+1,cutoff+97))
        if i==0:
            direct=model.predict(PredictRequest(**payload))
            np.testing.assert_allclose(direct['predictions'],response['predictions'],rtol=1e-6,atol=1e-6)
        if i%20==0:print(f'Real requests completed: {i+1}/100',flush=True)
    # Wrong schema must be refused rather than producing a plausible curve.
    payload['channel_ids'][0]='not-a-real-tag'
    assert client.post('/api/v1/predict',json=payload).status_code==422
    report={'source_sha256':dataset.file_sha256,'model_version':spec['model_version'],'requests':100,'input_shape':[96,56],'output_shape':[96,56],'device':'cpu','threads':2,'http_p50_ms':float(np.percentile(latencies,50)),'http_p95_ms':float(np.percentile(latencies,95)),'http_max_ms':max(latencies),'direct_http_agree':True,'invalid_schema_rejected':True,'raw_latencies_ms':latencies}
    (ROOT/'evidence/real-prediction.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='raw_latencies_ms'},indent=2),flush=True)

if __name__=='__main__':main()
