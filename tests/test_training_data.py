from datetime import datetime,timedelta
from types import SimpleNamespace
import importlib.util
import numpy as np
import pytest

def data_points(n):
    return SimpleNamespace(times=[datetime(2025,1,1)+timedelta(seconds=i) for i in range(n)],channels=['a'],model_channels=['a'],model_indices=[0],values=np.ones((n,1)),model_values=np.ones((n,1)),units=['V'],descriptions=['a'])

def test_training_windows_are_strictly_inside_chronological_partitions():
    assert importlib.util.find_spec('demo_server.datasets'), 'Training data admission is not implemented'
    from demo_server.datasets import training_profile
    report=training_profile(data_points(1280))
    assert [p['points'] for p in report['partitions']]==[768,256,256]
    assert [p['windows'] for p in report['partitions']]==[577,65,65]
    for p in report['partitions']:
        assert p['first_start']==p['start']
        assert p['last_start']+191==p['end']-1

def test_training_rejects_insufficient_evaluation_data():
    assert importlib.util.find_spec('demo_server.datasets'), 'Training data admission is not implemented'
    from demo_server.datasets import training_profile
    with pytest.raises(ValueError,match='223|32'):
        training_profile(data_points(1100))

def test_training_admission_requires_enough_time_for_frozen_fault_regression():
    from demo_server.datasets import training_profile
    with pytest.raises(ValueError,match='256|故障回归'):
        training_profile(data_points(1115))

def test_training_rejects_known_artificial_fault_workbook(tmp_path):
    assert importlib.util.find_spec('demo_server.datasets'), 'Training data admission is not implemented'
    from demo_server.datasets import read_training_excel
    from openpyxl import Workbook
    p=tmp_path/'injected.xlsx';w=Workbook();w.properties.keywords='SENSOR_DEMO_FAULT';w.save(p)
    with pytest.raises(ValueError,match='人工|故障'):
        read_training_excel(p)

def test_register_requires_normal_attestation_and_deduplicates(tmp_path,monkeypatch):
    assert importlib.util.find_spec('demo_server.datasets'), 'Training data admission is not implemented'
    from demo_server.datasets import DatasetRegistry
    import demo_server.datasets as datasets
    from pathlib import Path
    d=data_points(1280);d.file_sha256='f'*64;d.content_id='c'*64
    monkeypatch.setattr(datasets,'read_training_excel',lambda path:d)
    registry=DatasetRegistry(tmp_path)
    try:
        incoming=tmp_path/'input.xlsx';incoming.write_bytes(b'fixture')
        first=registry.submit(incoming,'normal.xlsx');registry.wait_idle()
        assert registry.get(first['dataset_id'])['status']=='validated'
        with pytest.raises(ValueError,match='正常'):
            registry.register(first['dataset_id'],False)
        assert registry.register(first['dataset_id'],True)['status']=='ready'
        second=tmp_path/'copy.xlsx';second.write_bytes(b'fixture')
        duplicate=registry.submit(second,'renamed.xlsx');registry.wait_idle()
        report=registry.get(duplicate['dataset_id'])
        assert report['status']=='duplicate' and report['duplicate_of']==first['dataset_id']
        with pytest.raises(ValueError):registry.register(duplicate['dataset_id'],True)
        d.content_id='d'*64
        overlap=tmp_path/'overlap.xlsx';overlap.write_bytes(b'modified fixture')
        overlap_id=registry.submit(overlap,'overlap.xlsx')['dataset_id'];registry.wait_idle()
        assert registry.get(overlap_id)['status']=='rejected'
        assert '重叠' in registry.get(overlap_id)['error']
    finally:registry.close()
