import importlib.util
import numpy as np

def test_calibration_only_passes_history_to_predictor():
    assert importlib.util.find_spec('demo_server.calibration') is not None,'Normal calibration is not implemented'
    from demo_server.calibration import calibrate_thresholds
    values=np.repeat(np.arange(400.)[:,None],2,axis=1)
    seen=[]
    class Predictor:
        def predict(self,x,channels):
            seen.append(x.copy())
            return np.repeat(x[:,-1:,:],96,axis=1)
    result=calibrate_thresholds(Predictor(),values,[200,300],['a','b'],np.zeros(2))
    assert np.allclose(result['thresholds'],[1,1])
    assert np.array_equal(seen[0][0],values[105:201])
    assert np.array_equal(seen[0][1],values[205:301])
