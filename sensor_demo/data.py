from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import hashlib,json,zipfile
import numpy as np
from openpyxl import load_workbook
from .config import ROOT

@dataclass
class SensorDataset:
    path: Path
    file_sha256: str
    content_id: str
    channels: list[str]
    descriptions: list[str]
    units: list[str]
    groups: list[str]
    times: list[datetime]
    values: np.ndarray
    model_channels: list[str]
    model_indices: list[int]

    @property
    def model_values(self): return self.values[:,self.model_indices]

def channel_group(tag):
    if tag.startswith('SCR') or tag in ('avg','max'): return '电气'
    if tag.startswith('TT'): return '温度'
    if tag.startswith('AT'): return '气体组分'
    if tag.startswith('FT'): return '流量'
    if tag.startswith(('LT','LGT')) or 'Level' in tag or tag=='PIC010306_PV': return '液位'
    if tag.startswith(('PT','PDT')): return '压力'
    return '其他'

def channel_unit(tag,description):
    if tag=='SCRDLFK': return 'A'
    if tag=='SCRDYFK': return 'V'
    if '%' in description: return '%'
    if 'mm' in description: return 'mm'
    # Missing physical units remain explicit rather than assuming all pressures use MPa.
    return '原始单位'

def read_excel(path,progress=None,reject_faults=False):
    path=Path(path)
    if path.suffix.lower()!='.xlsx': raise ValueError('请选择 .xlsx 格式的 Excel 文件')
    if not path.is_file(): raise ValueError('文件不存在，请重新选择')
    if path.stat().st_size>50*1024*1024: raise ValueError('文件超过50MB限制')
    try:
        with zipfile.ZipFile(path) as z:
            if sum(x.file_size for x in z.infolist())>256*1024*1024: raise ValueError('解压数据超过256MB限制')
        workbook=load_workbook(path,read_only=True,data_only=True)
    except (OSError,zipfile.BadZipFile) as exc: raise ValueError('无法读取工作簿，请确认是有效xlsx文件') from exc
    try:
        if reject_faults and 'SENSOR_DEMO_FAULT' in (workbook.properties.keywords or ''):
            raise ValueError('已知人工故障数据不能进入正常训练池，请选择原始正常Excel。')
        ws=workbook.active; iterator=ws.iter_rows(values_only=True)
        header=next(iterator,None)
        if not header: raise ValueError('工作簿为空，没有表头和数据')
        header=[str(x).strip() if x is not None else '' for x in header]
        if len([x for x in header if x])!=len(set(x for x in header if x)): raise ValueError('表头包含重复测点')
        schema=json.loads((ROOT/'configs/model-schema.json').read_text(encoding='utf-8'))
        model_channels=schema['model_channels']
        if 'LocalTimeCol' in header:
            time_index=header.index('LocalTimeCol')
            indices=list(range(5,66))
            if len(header)<66: raise ValueError('SensorData缺少F至BN的61个测点')
            description_row=next(iterator,()); repeated=next(iterator,())
            descriptions=[str(description_row[i] or header[i]).replace('\t',' ') for i in indices]
            if list(repeated[:len(header)])!=list(next(ws.iter_rows(min_row=1,max_row=1,values_only=True))): raise ValueError('三行表头结构与SensorData格式不一致')
            first_row=4
        elif header[0] in ('source_time','time','时间'):
            time_index=0;indices=list(range(1,len(header)));descriptions=[header[i] for i in indices];first_row=2
        else: raise ValueError('无法识别表结构：需要SensorData三行表头，或source_time及测点ID表头')
        channels=[header[i] for i in indices]
        missing=[c for c in model_channels if c not in channels]
        if missing: raise ValueError('缺少模型通道：'+', '.join(missing[:8]))
        times=[]; rows=[]
        for n,row in enumerate(iterator,start=first_row):
            if n>200003: raise ValueError('数据超过200000行限制')
            if all(x is None for x in row): continue
            try:
                t=row[time_index]
                if not isinstance(t,datetime): t=datetime.fromisoformat(str(t))
                if t.tzinfo is not None: raise ValueError('应使用本地无时区源时间')
                v=[float(row[i]) for i in indices]
                if not np.isfinite(v).all(): raise ValueError('含NaN或无穷值')
            except (ValueError,TypeError,IndexError) as exc: raise ValueError(f'第{n}行时间或测量值无效：{exc}') from exc
            if times and (t-times[-1]).total_seconds()!=1: raise ValueError(f'第{n}行不是连续1秒采样，存在缺测、重复或乱序')
            times.append(t);rows.append(v)
            if progress and len(rows)%2000==0: progress(len(rows))
        if not rows: raise ValueError('没有有效测量数据')
        values=np.asarray(rows,dtype=np.float64);values.setflags(write=False)
        model_indices=[channels.index(c) for c in model_channels]
        digest=hashlib.sha256()
        digest.update('\n'.join(t.isoformat() for t in times).encode())
        digest.update(values[:,model_indices].astype('<f8').tobytes())
        return SensorDataset(path,hashlib.sha256(path.read_bytes()).hexdigest(),digest.hexdigest(),channels,descriptions,[channel_unit(c,d) for c,d in zip(channels,descriptions)],[channel_group(c) for c in channels],times,values,model_channels,model_indices)
    finally: workbook.close()
