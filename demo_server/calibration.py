import numpy as np

def calibrate_thresholds(predictor,values,cutoffs,channels,floors):
    values=np.asarray(values,dtype=float);floors=np.asarray(floors,dtype=float)
    errors=[]
    for begin in range(0,len(cutoffs),16):
        batch=cutoffs[begin:begin+16]
        if any(c<95 or c+1>=len(values) for c in batch):raise ValueError('校准窗口超出数据范围')
        history=np.stack([values[c-95:c+1] for c in batch])
        forecast=predictor.predict(history,channels=channels)
        errors.extend(np.abs(np.asarray(forecast)[:,0,:]-values[np.asarray(batch)+1]))
    quantiles=np.quantile(np.asarray(errors),.99,axis=0)
    thresholds=np.maximum(quantiles,floors)
    return {'thresholds':thresholds.tolist(),'normal_abs_quantile99':quantiles.tolist(),'minimum_deviation':floors.tolist(),'samples':len(errors),'forecast_offset':1}
