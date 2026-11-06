from pathlib import Path
import importlib.util
import hashlib
import pytest
from openpyxl import Workbook

def importer():
    assert importlib.util.find_spec('sensor_demo.data') is not None,'Excel importer not implemented'
    from sensor_demo.data import read_excel
    return read_excel

def test_real_excel_schema_and_no_mutation():
    read=importer()
    p=Path(__file__).resolve().parents[1]/'sample_data/SensorData.xlsx'
    before=hashlib.sha256(p.read_bytes()).hexdigest()
    data=read(p)
    assert data.values.shape==(24234,61)
    assert len(data.model_channels)==56
    assert len(data.model_indices)==56
    assert data.times[0].isoformat()=='2025-03-30T11:02:00'
    assert data.times[-1].isoformat()=='2025-03-30T17:45:53'
    assert all((b-a).total_seconds()==1 for a,b in zip(data.times,data.times[1:]))
    assert 'FT010006' not in data.model_channels
    assert before==hashlib.sha256(p.read_bytes()).hexdigest()

def test_invalid_files_and_missing_channels(tmp_path):
    read=importer()
    wrong=tmp_path/'wrong.txt';wrong.write_text('abc')
    with pytest.raises(ValueError,match='xlsx'):read(wrong)
    p=tmp_path/'empty.xlsx';w=Workbook();w.save(p)
    with pytest.raises(ValueError):read(p)

def test_duplicate_headers_and_nonfinite_data(tmp_path):
    read=importer()
    p=tmp_path/'bad.xlsx';w=Workbook();ws=w.active
    ws.append(['time','SCRDLFK','SCRDLFK']);ws.append(['2025-01-01 00:00:00',1,2]);w.save(p)
    with pytest.raises(ValueError):read(p)
