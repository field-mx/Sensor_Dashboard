from pathlib import Path
import argparse,hashlib,json,time,traceback
import numpy as np
from filelock import FileLock
from sensor_demo.config import ROOT
from .jobs import JobStore
from .versions import predictor_from_bundle
from .model import MODEL_ROOT,BASELINE
from .contracts import CHANNELS
from .validation import assess_metrics,forecast_mae,fault_regression
from .calibration import calibrate_thresholds

def evaluate_job(root,job_id):
    import torch
    torch.set_num_threads(2);store=JobStore(root);job=store.get(job_id);started=time.perf_counter();candidate=job['candidate'];recipe=job['evaluation_recipe']
    def update(message):
        if store.get(job_id).get('cancel_requested'):raise InterruptedError('用户取消候选验证')
        store.patch(job_id,message=message,evaluation_elapsed_seconds=round(time.perf_counter()-started,2))
    with FileLock(str(root/'training.lock'),timeout=0):
        with store.connect() as db:dataset=json.loads(db.execute('SELECT document FROM datasets WHERE id=?',(job['dataset_id'],)).fetchone()[0])
        pool=root/'datasets'/job['dataset_id'];samples=pool/'samples.npz'
        if hashlib.sha256(samples.read_bytes()).hexdigest()!=dataset['samples_sha256']:raise ValueError('评估数据完整性不一致')
        with np.load(samples,allow_pickle=False) as archive:values=archive['values'].copy();channels=archive['channels'].tolist()
        if channels!=CHANNELS:raise ValueError('评估通道顺序错误')
        baseline_entry=json.loads((MODEL_ROOT/'weights/manifest.json').read_text(encoding='utf-8'))['models']['industrial']
        update('真实评估：加载基线与候选、校验哈希并预热')
        baseline=predictor_from_bundle(MODEL_ROOT/baseline_entry['path'],baseline_entry['sha256']);predictor=predictor_from_bundle(root/candidate['path'],candidate['sha256'])
        starts=job['window_starts'][2]
        update('真实评估：冻结评估窗口，计算公开基线96步MAE')
        baseline_mae=forecast_mae(baseline,values,starts,lambda:update('真实评估：计算公开基线96步MAE'))
        candidate_mae=forecast_mae(predictor,values,starts,lambda:update('真实评估：计算候选96步MAE'))
        partition=dataset['partitions'][1];cutoffs=np.linspace(partition['start']+95,partition['end']-2,recipe['calibration_windows'],dtype=int).tolist()
        update('真实评估：只用正常验证段校准候选预警阈值')
        floors=predictor.scale.detach().numpy()*recipe['calibration_floor_raw_scale_ratio']
        calibration=calibrate_thresholds(predictor,values,cutoffs,CHANNELS,floors)
        calibration.update(model_version=candidate['version'],source_sha256=dataset['file_sha256'],calibration_partition=[partition['start'],partition['end']],cutoff_seqs=cutoffs,channels=CHANNELS,method='max(normal absolute one-step residual q99, training raw_scale * 0.05)',assumption=dataset['limitations'])
        calibration['calibration_id']=hashlib.sha256(json.dumps(calibration,sort_keys=True).encode()).hexdigest()
        fault_start=dataset['partitions'][2]['start']+96
        faults=fault_regression(predictor,values,fault_start,calibration['thresholds'],recipe['fault'],lambda amp,seq:update(f'真实预警回归：{recipe["fault"]["channel"]} {amp:+g}，已计算{seq+1}/{recipe["fault"]["steps"]}个因果预测'))
        scale=baseline.scale.detach().numpy();result=assess_metrics(CHANNELS,baseline_mae/scale,candidate_mae/scale,recipe,all(f['passed'] for f in faults))
        result.update(recipe=recipe,recipe_sha256=job['evaluation_recipe_sha256'],dataset_id=job['dataset_id'],content_id=dataset['content_id'],evaluation_starts=starts,baseline_version=BASELINE,baseline_sha256=baseline_entry['sha256'],channels=CHANNELS,baseline_raw_mae=baseline_mae.tolist(),candidate_raw_mae=candidate_mae.tolist(),fault_start_seq=fault_start,faults=faults,elapsed_seconds=round(time.perf_counter()-started,2),limitations=dataset['limitations'])
        record={**candidate,'eligible':result['eligible'],'status':'eligible' if result['eligible'] else 'rejected','validation':result,'calibration':calibration}
        folder=root/'jobs'/job_id;(folder/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');(folder/'candidate.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        update('候选验证完成；尚未激活')
        with store.connect() as db:db.execute('INSERT OR REPLACE INTO model_versions VALUES (?,?)',(record['version'],json.dumps(record,ensure_ascii=False)))
        store.patch(job_id,status='succeeded' if result['eligible'] else 'rejected',candidate=record,message='候选通过门槛，可以激活' if result['eligible'] else '候选未通过门槛：'+'；'.join(result['reasons']))

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',required=True,type=Path);p.add_argument('--job',required=True);args=p.parse_args();store=JobStore(args.root)
    try:evaluate_job(args.root,args.job)
    except InterruptedError as exc:store.patch(args.job,status='cancelled',message=str(exc)+'；旧模型保留')
    except Exception as exc:
        folder=args.root/'jobs'/args.job;folder.mkdir(parents=True,exist_ok=True);(folder/'evaluation-error.log').write_text(traceback.format_exc(),encoding='utf-8')
        store.patch(args.job,status='failed',error='候选验证失败：'+str(exc),message='旧活动模型保留')

if __name__=='__main__':main()
