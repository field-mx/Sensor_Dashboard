from uuid import uuid4
import numpy as np

class FaultInjector:
    def __init__(self):self.fault=None
    def start(self,channel,amplitude,duration,start_seq):
        if duration<1 or not np.isfinite(amplitude):raise ValueError('故障幅度和持续时间无效')
        self.fault={'channel':int(channel),'amplitude':float(amplitude),'duration':int(duration),'start_seq':int(start_seq)}
    def stop(self):self.fault=None
    def apply(self,seq,source):
        values=np.asarray(source,dtype=float).copy()
        f=self.fault
        if f and f['start_seq']<=seq<f['start_seq']+f['duration']:values[f['channel']]+=f['amplitude']
        return values
    def active_at(self,seq):
        f=self.fault
        return bool(f and f['start_seq']<=seq<f['start_seq']+f['duration'])

class AlarmEngine:
    """Consumes only observations and earlier forecasts; never fault labels."""
    def __init__(self,channels,thresholds):
        self.channels=list(channels);self.thresholds=np.asarray(thresholds,dtype=float)
        if self.thresholds.shape!=(len(channels),) or not np.isfinite(self.thresholds).all() or not (self.thresholds>0).all():raise ValueError('预警阈值无效')
        self.reset()
    def reset(self):
        self.events=[];self.active={};self.high=np.zeros(len(self.channels),dtype=int)
        self.low=np.zeros(len(self.channels),dtype=int)
        self.statuses=['unavailable']*len(self.channels);self.residuals=None;self.last_seq=None
    def update(self,seq,observed,predicted):
        if predicted is None:
            self.statuses=['unavailable']*len(self.channels);self.high[:]=0;self.low[:]=0;self.residuals=None;self.last_seq=seq;return
        obs=np.asarray(observed);pred=np.asarray(predicted)
        if obs.shape!=self.thresholds.shape or pred.shape!=obs.shape or not np.isfinite(obs).all() or not np.isfinite(pred).all():raise ValueError('残差输入无效')
        if self.last_seq is not None and seq!=self.last_seq+1:self.high[:]=0;self.low[:]=0
        self.last_seq=seq;self.residuals=np.abs(obs-pred)
        for i,value in enumerate(self.residuals):
            if value>self.thresholds[i]:
                self.low[i]=0
                self.high[i]+=1
                if self.high[i]>=3 and i not in self.active:
                    event={'id':str(uuid4()),'channel':self.channels[i],'start_seq':seq-2,'alarm_seq':seq,'last_seq':seq,'residual':float(value),'threshold':float(self.thresholds[i]),'status':'active','recovery_seq':None}
                    self.events.append(event);self.active[i]=event
                self.statuses[i]='alarm' if i in self.active else 'warning'
            else:
                self.high[i]=0
                if i in self.active:
                    self.low[i]+=1
                    if self.low[i]>=5:
                        event=self.active.pop(i);event['status']='recovered';event['recovery_seq']=seq;event['last_seq']=seq
                else:self.low[i]=0
                self.statuses[i]='alarm' if i in self.active else 'normal'
            if i in self.active:self.active[i]['last_seq']=seq
