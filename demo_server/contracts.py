from datetime import datetime
import json
import numpy as np
from pydantic import BaseModel,Field,model_validator
from sensor_demo.config import ROOT

SCHEMA_ID='prctf-industrial-56-v1'
CHANNELS=json.loads((ROOT/'configs/model-schema.json').read_text(encoding='utf-8'))['model_channels']

class PredictRequest(BaseModel):
    run_id:str=Field(min_length=1,max_length=80)
    generation:int=Field(ge=0)
    request_id:str=Field(min_length=1,max_length=80)
    model_version:str
    schema_id:str
    cutoff_seq:int=Field(ge=95)
    source_times:list[datetime]
    channel_ids:list[str]
    history:list[list[float]]
    valid:list[list[bool]]|None=None
    @model_validator(mode='after')
    def check_window(self):
        if self.schema_id!=SCHEMA_ID:raise ValueError('不匹配的schema')
        if len(self.channel_ids)!=56 or len(set(self.channel_ids))!=56 or set(self.channel_ids)!=set(CHANNELS):raise ValueError('输入必须恰好包含56个模型通道')
        values=np.asarray(self.history,dtype=float)
        if values.shape!=(96,56) or not np.isfinite(values).all():raise ValueError('需要有限数值矩阵[96,56]')
        if len(self.source_times)!=96 or any((b-a).total_seconds()!=1 for a,b in zip(self.source_times,self.source_times[1:])):raise ValueError('历史必须是连续96个1秒点')
        if self.valid is not None and (np.asarray(self.valid).shape!=(96,56) or not np.asarray(self.valid).all()):raise ValueError('模型不支持缺测窗口')
        return self
