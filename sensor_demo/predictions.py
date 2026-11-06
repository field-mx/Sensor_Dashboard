import numpy as np

class PredictionLedger:
    def __init__(self):self.reset('',0,'')
    def reset(self,run_id,generation,version):
        self.run_id=run_id;self.generation=generation;self.version=version;self.targets={};self.latest=None
    def add(self,response):
        if (response['run_id'],response['generation'],response['model_version'])!=(self.run_id,self.generation,self.version):return False
        cutoff=response['cutoff_seq'];targets=response['target_seqs'];values=np.asarray(response['predictions'],dtype=float)
        if targets!=list(range(cutoff+1,cutoff+97)) or values.shape!=(96,56) or not np.isfinite(values).all():raise ValueError('预测返回的时间或矩阵无效')
        for seq,row in zip(targets,values):
            prior=self.targets.get(seq)
            if prior is None or prior[0]<cutoff:self.targets[seq]=(cutoff,row.copy())
        self.targets={seq:v for seq,v in self.targets.items() if seq>cutoff-360}
        self.latest=response
        return True
    def for_target(self,seq):
        item=self.targets.get(seq)
        return item[1] if item and item[0]<seq else None
