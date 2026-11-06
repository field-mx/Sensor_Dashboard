import numpy as np
from sensor_demo.alarms import AlarmEngine

def assess_metrics(channels,baseline,candidate,recipe,fault_passed):
    baseline=np.asarray(baseline,dtype=float);candidate=np.asarray(candidate,dtype=float)
    if baseline.shape!=(len(channels),) or candidate.shape!=baseline.shape or not np.isfinite(baseline).all() or not np.isfinite(candidate).all() or (baseline<0).any() or (candidate<0).any():raise ValueError('评估指标包含无效或非有限结果')
    overall_limit=float(baseline.mean()*recipe['overall_max_ratio']);overall_passed=bool(candidate.mean()<=overall_limit)
    critical=[];reasons=[]
    if not overall_passed:reasons.append('总体归一化MAE超过预设1.02倍门槛')
    for channel,tolerance in recipe['critical_absolute_normalized_mae'].items():
        index=channels.index(channel);limit=float(baseline[index]*recipe['critical_max_ratio']+tolerance);passed=bool(candidate[index]<=limit)
        critical.append({'channel':channel,'baseline':float(baseline[index]),'candidate':float(candidate[index]),'limit':limit,'absolute_tolerance':tolerance,'passed':passed})
        if not passed:reasons.append(channel+'关键通道MAE超过预设门槛')
    critical_passed=all(c['passed'] for c in critical)
    if not fault_passed:reasons.append('固定突高/突低残差预警回归未通过')
    return {'eligible':overall_passed and critical_passed and bool(fault_passed),'overall_passed':overall_passed,'overall_baseline':float(baseline.mean()),'overall_candidate':float(candidate.mean()),'overall_limit':overall_limit,'critical_passed':critical_passed,'critical':critical,'fault_passed':bool(fault_passed),'reasons':reasons}

def forecast_mae(predictor,values,starts,callback):
    total=np.zeros(values.shape[1]);count=0
    for begin in range(0,len(starts),4):
        positions=starts[begin:begin+4];history=np.stack([values[i:i+96] for i in positions]);target=np.stack([values[i+96:i+192] for i in positions])
        output=predictor.predict(history,channels=predictor.channels)
        total+=np.abs(output-target).sum(axis=(0,1));count+=len(positions)*96;callback()
    return total/count

def fault_regression(predictor,values,start,thresholds,recipe,callback):
    if start<96 or start+recipe['steps']>len(values):raise ValueError('故障回归数据不足：需要96秒历史和完整的固定回放区间，请使用更长的正常Excel。')
    results=[];index=predictor.channels.index(recipe['channel'])
    for amplitude in recipe['amplitudes']:
        observed=list(values[start-96:start].copy());engine=AlarmEngine(predictor.channels,thresholds)
        for seq in range(recipe['steps']):
            # Predict before revealing this step's observation, including the injected history.
            predicted=predictor.predict(np.stack(observed[-96:]),channels=predictor.channels)[0]
            current=values[start+seq].copy()
            if seq<recipe['duration']:current[index]+=amplitude
            engine.update(seq,current,predicted);observed.append(current)
            if seq%8==0:callback(amplitude,seq)
        events=[e for e in engine.events if e['channel']==recipe['channel']]
        first=events[0] if events else None
        passed=bool(first and first['alarm_seq']==recipe['alarm_points']-1 and first['status']=='recovered' and first['recovery_seq']>=recipe['duration']+recipe['recovery_points']-1)
        results.append({'channel':recipe['channel'],'amplitude':amplitude,'duration':recipe['duration'],'steps':recipe['steps'],'passed':passed,'event':first})
    return results
