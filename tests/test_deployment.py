import json,os,subprocess,sys
from pathlib import Path

def test_clean_default_uses_bundled_workbook_and_external_runtime(tmp_path):
    from sensor_demo.config import ROOT
    env={**os.environ,'LOCALAPPDATA':str(tmp_path)};env.pop('SENSOR_DEMO_DATA_DIR',None)
    code='from sensor_demo.config import DEFAULT_DATA,RUNTIME; import json; print(json.dumps([str(DEFAULT_DATA),str(RUNTIME)]))'
    result=subprocess.run([sys.executable,'-c',code],env=env,cwd=ROOT,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    data,runtime=map(Path,json.loads(result.stdout))
    assert data==ROOT/'sample_data/SensorData.xlsx' and data.exists()
    assert runtime==tmp_path/'SensorDashboardDemo/runtime'

def test_backend_instance_lock_rejects_same_data_directory(tmp_path):
    from demo_server.instance import BackendInstance
    from filelock import Timeout
    import pytest
    with BackendInstance(tmp_path):
        with pytest.raises(Timeout):
            with BackendInstance(tmp_path):pass
    with BackendInstance(tmp_path):pass
