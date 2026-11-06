"""Visible desktop launch; hidden local backend, supervised workers and durable logs."""
import argparse,hashlib,json,os,subprocess,sys,time,urllib.error,urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from .config import ROOT,RUNTIME,load_settings

def local_address():
    url=load_settings()['api_url'].rstrip('/');parsed=urlparse(url)
    if parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','localhost') or parsed.username or parsed.path or parsed.query or parsed.fragment:
        raise ValueError('本机后台地址需为 http://127.0.0.1:端口；请在界面修改或检查client.json。')
    return url,parsed.port or 80

def identity():return hashlib.sha256((str(ROOT)+'|'+str(RUNTIME)).encode()).hexdigest()

def health(url):
    try:
        with urllib.request.urlopen(url+'/api/v1/health',timeout=1) as reply:data=json.load(reply)
    except (urllib.error.URLError,TimeoutError,ConnectionError):return None
    except ValueError as exc:raise RuntimeError('端口被其他服务占用，请在client.json改用空闲端口。') from exc
    if data.get('app_id')!='sensor-dashboard-demo' or data.get('instance_id')!=identity():
        raise RuntimeError('此端口正在运行其他目录/数据目录的后台，请先停止它或更换端口。')
    return data

def spawn(module,*args):
    logs=RUNTIME/'logs';logs.mkdir(parents=True,exist_ok=True)
    path=logs/(module+'-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.log')
    env={**os.environ,'PYTHONIOENCODING':'utf-8','PYTHONUNBUFFERED':'1'}
    flags=(getattr(subprocess,'CREATE_NO_WINDOW',0)|getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0)) if os.name=='nt' else 0
    with path.open('ab') as stream:
        process=subprocess.Popen([sys.executable,'-m',module,*args],cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=stream,stderr=subprocess.STDOUT,creationflags=flags,start_new_session=os.name!='nt')
    return process,path

def ensure_backend():
    url,port=local_address();data=health(url)
    if data:return data
    process,path=spawn('demo_server','--port',str(port));deadline=time.monotonic()+90
    print('正在加载本机模型，请稍候…',flush=True)
    while time.monotonic()<deadline:
        data=health(url)
        if data:return data
        if process.poll() is not None:raise RuntimeError('后台启动失败，请查看：'+str(path))
        time.sleep(.25)
    raise RuntimeError('后台启动超过90秒，请查看：'+str(path))

def main():
    parser=argparse.ArgumentParser(description='Windows本机Demo启动和停止')
    parser.add_argument('action',choices=['launch','frontend','backend','status','stop'])
    action=parser.parse_args().action
    try:
        if action in ('launch','backend'):
            data=ensure_backend();print('本机后台已就绪：'+data['model_version'])
        if action in ('launch','frontend'):
            process,path=spawn('sensor_demo');print('界面已启动；日志：'+str(path))
        if action=='status':print(json.dumps({'backend':health(local_address()[0]),'data_dir':str(RUNTIME)},ensure_ascii=False,indent=2))
        if action=='stop':
            url,_=local_address()
            if not health(url):print('后台未运行。');return 0
            request=urllib.request.Request(url+'/api/v1/control/shutdown',data=b'{}',headers={'Content-Type':'application/json'},method='POST')
            with urllib.request.urlopen(request,timeout=5) as reply:print(json.load(reply)['message'])
        return 0
    except urllib.error.HTTPError as exc:
        try:message=json.load(exc)['detail']
        except (ValueError,KeyError):message=str(exc)
        print('操作失败：'+str(message),file=sys.stderr);return 1
    except (OSError,ValueError,RuntimeError) as exc:print('操作失败：'+str(exc),file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
