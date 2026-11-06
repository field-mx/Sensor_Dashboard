"""Independent CPU finetuning; never calls the inference-only predictor to train."""
from pathlib import Path
import argparse,copy,hashlib,json,os,time,traceback,math
import numpy as np
from filelock import FileLock
from .jobs import JobStore
from .bundles import load_bundle_model
from .contracts import CHANNELS

class Cancelled(Exception):pass

def selected_starts(partition,budget):
    return np.linspace(partition['first_start'],partition['last_start'],min(budget,partition['windows']),dtype=int).tolist()

def train_job(root,job_id):
    import torch
    torch.set_num_threads(2);torch.manual_seed(42);np.random.seed(42)
    store=JobStore(root);job=store.get(job_id);started=time.perf_counter();folder=Path(root)/'jobs'/job_id;folder.mkdir(parents=True,exist_ok=True)
    def check():
        if store.get(job_id).get('cancel_requested'):raise Cancelled()
    def update(**values):
        check();return store.patch(job_id,elapsed_seconds=round(time.perf_counter()-started,2),worker_pid=os.getpid(),**values)
    with FileLock(str(Path(root)/'training.lock'),timeout=0):
        update(status='validating',message='校验冻结训练数据和基线权重')
        with store.connect() as db:row=db.execute('SELECT document FROM datasets WHERE id=?',(job['dataset_id'],)).fetchone()
        if not row:raise ValueError('训练数据不存在')
        data=json.loads(row[0]);pool=Path(root)/'datasets'/job['dataset_id'];samples=pool/'samples.npz'
        if data['status']!='ready' or not data.get('normal_attested'):raise ValueError('训练数据尚未正常登记')
        if hashlib.sha256(samples.read_bytes()).hexdigest()!=data['samples_sha256'] or hashlib.sha256((pool/'source.xlsx').read_bytes()).hexdigest()!=data['file_sha256']:raise ValueError('训练池文件被修改，拒绝训练')
        with np.load(samples,allow_pickle=False) as archive:
            if archive['channels'].tolist()!=CHANNELS:raise ValueError('训练池通道顺序不匹配')
            values=torch.from_numpy(archive['values'].astype(np.float32))
        bundle,model=load_bundle_model(job['base_path'],job['base_sha256'])
        calibrated={name:value.clone() for name,value in model.named_buffers() if name in ('raw_mean','raw_scale','eq_mean','eq_scale','gain','src','dst','rid','feature_idx','lag_forbidden','lags','history_index')}
        if not torch.isfinite(model.raw_scale).all() or not (model.raw_scale>0).all():raise ValueError('模型归一化参数无效')
        normalized=(values-model.raw_mean)/model.raw_scale
        starts=[selected_starts(p,b) for p,b in zip(data['partitions'],job['window_budget'])]
        learning_rate=float(bundle['config']['learning_rate'])*.1
        optimizer=torch.optim.Adam(model.parameters(),lr=learning_rate)
        batch_size=job['batch_size'];total=(math.ceil(len(starts[0])/batch_size)+math.ceil(len(starts[1])/batch_size))*job['epochs'];completed=0;history=[];best_loss=float('inf');best_state=None;best_epoch=None
        update(status='training',learning_rate=learning_rate,window_starts=starts,actual_window_counts=[len(s) for s in starts],total_steps=total,completed_steps=0,message='真实微调进行中；候选尚未激活')
        def batches(indices):
            for offset in range(0,len(indices),batch_size):
                chosen=indices[offset:offset+batch_size]
                yield torch.stack([normalized[i:i+96] for i in chosen]),torch.stack([normalized[i+96:i+192] for i in chosen])
        for epoch in range(1,job['epochs']+1):
            model.train();loss_sum=0.;seen=0
            for x,y in batches(starts[0]):
                check();optimizer.zero_grad(set_to_none=True)
                with torch.enable_grad():
                    output=model(x,None,None,None);loss=torch.nn.functional.mse_loss(output,y)
                    if not torch.isfinite(loss):raise ValueError('训练loss非有限值，保留旧模型')
                    loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1.);optimizer.step()
                if not seen:store.mark_consumed(job,data)
                loss_sum+=float(loss.detach())*len(x);seen+=len(x);completed+=1
                update(epoch=epoch,phase='训练',train_loss=loss_sum/seen,completed_steps=completed,progress=round(completed/total*100,1))
            model.eval();validation_sum=0.;validation_seen=0
            with torch.no_grad():
                for x,y in batches(starts[1]):
                    check();loss=torch.nn.functional.mse_loss(model(x,None,None,None),y)
                    if not torch.isfinite(loss):raise ValueError('验证loss非有限值')
                    validation_sum+=float(loss)*len(x);validation_seen+=len(x);completed+=1
                    update(phase='验证',completed_steps=completed,progress=round(completed/total*100,1))
            validation_loss=validation_sum/validation_seen;history.append({'epoch':epoch,'train_loss':loss_sum/seen,'validation_loss':validation_loss,'elapsed_seconds':round(time.perf_counter()-started,2)})
            if validation_loss<best_loss:best_loss=validation_loss;best_state=copy.deepcopy(model.state_dict());best_epoch=epoch
            update(validation_loss=validation_loss,history=history)
        check();model.load_state_dict(best_state)
        for name,frozen in calibrated.items():
            if not torch.equal(dict(model.named_buffers())[name],frozen):raise ValueError('固定物理图或校准参数发生变化，拒绝候选')
        changed=sum(not torch.equal(bundle['state_dict'][name],value) for name,value in model.named_parameters())
        if not changed:raise ValueError('训练没有产生参数更新，拒绝伪更新')
        metadata={'job_id':job_id,'dataset_id':job['dataset_id'],'content_id':data['content_id'],'base_version':job['base_version'],'base_sha256':job['base_sha256'],'recipe_id':job['recipe_id'],'epochs':job['epochs'],'best_epoch':best_epoch,'learning_rate':learning_rate,'batch_size':batch_size,'window_starts':starts,'history':history,'changed_parameter_count':changed,'torch_version':str(torch.__version__),'elapsed_seconds':round(time.perf_counter()-started,2),'limitations':data['limitations']}
        candidate=copy.deepcopy(bundle);candidate['state_dict']={k:v.detach().cpu().clone() for k,v in best_state.items()};candidate['demo_finetuning']=metadata
        version='demo-'+job_id[:12];output=folder/'candidate.pt';tmp=folder/'candidate.tmp';torch.save(candidate,tmp);tmp.replace(output)
        sha=hashlib.sha256(output.read_bytes()).hexdigest()
        manifest={**metadata,'version':version,'path':str(output.relative_to(root)),'sha256':sha,'bytes':output.stat().st_size,'status':'trained','eligible':False}
        (folder/'candidate.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        update(status='trained',progress=100,candidate=manifest,message='训练完成，候选待验证；当前活动模型未改变')

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--job',required=True);args=p.parse_args();store=JobStore(args.root)
    try:train_job(args.root,args.job)
    except Cancelled:store.patch(args.job,status='cancelled',message='已在安全步骤取消；旧活动模型保留')
    except Exception as exc:
        folder=args.root/'jobs'/args.job;folder.mkdir(parents=True,exist_ok=True);(folder/'error.log').write_text(traceback.format_exc(),encoding='utf-8')
        store.patch(args.job,status='failed',error=str(exc) or type(exc).__name__,message='训练失败，旧活动模型保留')

if __name__=='__main__':main()
