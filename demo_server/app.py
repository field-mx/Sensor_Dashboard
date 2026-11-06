from contextlib import asynccontextmanager
from fastapi import FastAPI,HTTPException,Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from .contracts import PredictRequest
from .model import ModelService
from .datasets import DatasetRegistry
from .jobs import JobService
from .versions import VersionService
from .scheduling import ScheduleService
from .model import BASELINE
from filelock import Timeout
from pydantic import BaseModel
from uuid import uuid4
from .instance import BackendInstance
from sensor_demo.config import ROOT,RUNTIME
import os,hashlib

@asynccontextmanager
async def lifespan(app):
    with BackendInstance(RUNTIME):
        app.state.models=ModelService()
        app.state.datasets=DatasetRegistry()
        app.state.jobs=JobService(app.state.datasets,app.state.models)
        try:
            app.state.versions=VersionService(app.state.jobs)
            app.state.scheduling=ScheduleService(app.state.jobs)
            try:yield
            finally:app.state.scheduling.close()
        finally:app.state.jobs.close();app.state.datasets.close()

app=FastAPI(title='电解槽结项展示本机服务',lifespan=lifespan)

@app.exception_handler(RequestValidationError)
async def invalid_request(request,exc):
    return JSONResponse(status_code=422,content={'detail':'；'.join(error['msg'] for error in exc.errors())})

@app.get('/api/v1/health')
def health():
    m=app.state.models
    return {'ready':not m.busy,'training':m.busy,'model_version':m.version,'device':'cpu','app_id':'sensor-dashboard-demo','instance_id':hashlib.sha256((str(ROOT)+'|'+str(RUNTIME)).encode()).hexdigest(),'pid':os.getpid()}

@app.post('/api/v1/control/shutdown')
def shutdown():
    from .jobs import ACTIVE
    if any(j['status'] in ACTIVE for j in app.state.jobs.store.list()):raise HTTPException(409,'训练或验证正在运行，请等待完成或先在界面取消训练，再停止后台。')
    callback=getattr(app.state,'request_shutdown',None)
    if callback is None:raise HTTPException(503,'此启动方式不支持停止，请使用 python -m demo_server 启动。')
    callback();return {'message':'本机后台正在退出；再次启动Demo可恢复服务。'}

@app.get('/api/v1/model-spec')
def model_spec():return app.state.models.spec()

@app.post('/api/v1/predict')
def predict(request:PredictRequest):
    try:return app.state.models.predict(request)
    except RuntimeError as exc:raise HTTPException(503,str(exc))
    except ValueError as exc:raise HTTPException(409,str(exc))

@app.post('/api/v1/datasets',status_code=202)
async def upload_dataset(request:Request,filename:str='data.xlsx'):
    if not filename.lower().endswith('.xlsx'):raise HTTPException(422,'请选择 .xlsx 格式的正常数据')
    registry=app.state.datasets
    incoming=registry.root/('incoming-'+uuid4().hex+'.xlsx');size=0
    try:
        with incoming.open('xb') as stream:
            async for chunk in request.stream():
                size+=len(chunk)
                if size>50*1024*1024:raise HTTPException(413,'文件超过50MB限制')
                stream.write(chunk)
        if not size:raise HTTPException(422,'文件为空')
        return registry.submit(incoming,filename)
    finally:
        if incoming.exists():incoming.unlink()

@app.get('/api/v1/datasets')
def list_datasets():return {'datasets':app.state.datasets.list()}

@app.get('/api/v1/datasets/{dataset_id}')
def get_dataset(dataset_id:str):
    try:return app.state.datasets.get(dataset_id)
    except KeyError:raise HTTPException(404,'数据集不存在')

class RegisterDataset(BaseModel):
    normal_attested:bool=False

@app.post('/api/v1/datasets/{dataset_id}/register')
def register_dataset(dataset_id:str,body:RegisterDataset):
    try:return app.state.datasets.register(dataset_id,body.normal_attested)
    except KeyError:raise HTTPException(404,'数据集不存在')
    except ValueError as exc:raise HTTPException(422,str(exc))

class TrainRequest(BaseModel):
    dataset_id:str
    idempotency_key:str
    epochs:int=5

@app.post('/api/v1/training-jobs',status_code=202)
def create_training_job(body:TrainRequest):
    try:return app.state.jobs.create(body.dataset_id,body.idempotency_key,body.epochs)
    except KeyError:raise HTTPException(404,'数据集不存在')
    except ValueError as exc:raise HTTPException(409,str(exc))

@app.get('/api/v1/training-jobs')
def list_training_jobs():return {'jobs':app.state.jobs.store.list()}

@app.get('/api/v1/training-jobs/{job_id}')
def get_training_job(job_id:str):
    try:return app.state.jobs.store.get(job_id)
    except KeyError:raise HTTPException(404,'训练任务不存在')

@app.post('/api/v1/training-jobs/{job_id}/cancel')
def cancel_training_job(job_id:str):
    try:return app.state.jobs.cancel(job_id)
    except KeyError:raise HTTPException(404,'训练任务不存在')

@app.get('/api/v1/models')
def list_models():return {'active_version':app.state.models.version,'baseline_version':BASELINE,'versions':app.state.versions.list()}

@app.post('/api/v1/models/{version}/validate',status_code=202)
def validate_model(version:str):
    try:return app.state.versions.evaluate(version)
    except KeyError:raise HTTPException(404,'模型版本不存在')
    except ValueError as exc:raise HTTPException(409,str(exc))

@app.post('/api/v1/models/{version}/activate')
def activate_model(version:str):
    try:return app.state.versions.activate(version)
    except KeyError:raise HTTPException(404,'模型版本不存在')
    except Timeout:raise HTTPException(409,'模型资源正在使用，请稍后重试')
    except (ValueError,OSError) as exc:raise HTTPException(409,'激活失败，旧模型保留：'+str(exc))

@app.post('/api/v1/models/rollback')
def rollback_model():return activate_model(BASELINE)

class AutoTrainingConfig(BaseModel):
    enabled:bool=False
    local_time:str='02:00:00'

@app.get('/api/v1/auto-training')
def get_auto_training():return app.state.scheduling.get()

@app.put('/api/v1/auto-training')
def put_auto_training(body:AutoTrainingConfig):
    try:return app.state.scheduling.set_config(body.enabled,body.local_time)
    except ValueError as exc:raise HTTPException(422,str(exc))

@app.post('/api/v1/auto-training/check',status_code=202)
def check_auto_training():return app.state.scheduling.check('manual_check')
