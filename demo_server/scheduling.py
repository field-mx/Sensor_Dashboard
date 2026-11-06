from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from uuid import uuid4
import json,re,threading,time,sqlite3
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_MISSED
from .jobs import ACTIVE

ZONE=ZoneInfo('Asia/Shanghai')
DEFAULTS={'enabled':False,'local_time':'02:00:00','timezone':'Asia/Shanghai'}

class ScheduleService:
    def __init__(self,jobs,autostart=True):
        self.jobs=jobs;self.store=jobs.store;self.registry=jobs.registry;self.lock=threading.Lock();self.scheduler=None
        with self.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, document TEXT NOT NULL)')
            db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)',('auto_training',json.dumps(DEFAULTS)))
            db.execute('CREATE TABLE IF NOT EXISTS daily_runs (id TEXT PRIMARY KEY, slot TEXT UNIQUE, document TEXT NOT NULL)')
        # Upgrade existing completed/started manual jobs into the same consumed-data ledger.
        for job in self.store.list():
            if job.get('epoch',0)>0 or job['status'] in ('trained','succeeded','rejected'):
                try:self.store.mark_consumed(job,self.registry.get(job['dataset_id']))
                except KeyError:pass
        if autostart:
            self.record_missed(datetime.now(ZONE))
            self.scheduler=BackgroundScheduler(timezone=ZONE);self.scheduler.add_listener(self.on_missed,EVENT_JOB_MISSED);self.scheduler.start();self.apply_schedule()
    def save_missed(self,due,now,message):
        record={'id':uuid4().hex,'source':'auto','at':now.isoformat(),'scheduled_time':due.isoformat(),'status':'missed','message':message+'；不自动补训，可点击立即检查并后训练。'}
        with self.store.connect() as db:db.execute('INSERT OR IGNORE INTO daily_runs VALUES (?,?,?)',(record['id'],due.date().isoformat(),json.dumps(record,ensure_ascii=False)))
        return record
    def on_missed(self,event):
        if event.job_id=='daily-training':self.save_missed(event.scheduled_run_time.astimezone(ZONE),datetime.now(ZONE),'后台休眠或调度延迟超过1秒，已错过计划')
    def record_missed(self,now):
        config=self.config()
        if not config['enabled']:return
        h,m,s=map(int,config['local_time'].split(':'));due=now.replace(hour=h,minute=m,second=s,microsecond=0)
        if now<=due or config.get('updated_at',now.timestamp())>=due.timestamp():return
        self.save_missed(due,now,'后台未在计划时间运行，本次已错过')
    def config(self):
        with self.store.connect() as db:raw=db.execute('SELECT document FROM settings WHERE name=?',('auto_training',)).fetchone()[0]
        return json.loads(raw)
    def trigger(self,config):
        h,m,s=map(int,config['local_time'].split(':'));return CronTrigger(hour=h,minute=m,second=s,timezone=ZONE)
    def apply_schedule(self):
        if not self.scheduler:return
        if self.scheduler.get_job('daily-training'):self.scheduler.remove_job('daily-training')
        config=self.config()
        if config['enabled']:self.scheduler.add_job(self.check,self.trigger(config),args=['auto'],id='daily-training',replace_existing=True,max_instances=1,coalesce=True,misfire_grace_time=1)
    def set_config(self,enabled,local_time):
        if type(enabled) is not bool or not re.fullmatch(r'\d{2}:\d{2}(:\d{2})?',local_time):raise ValueError('执行时间需为HH:mm或HH:mm:ss')
        if len(local_time)==5:local_time+=':00'
        try:datetime.strptime(local_time,'%H:%M:%S')
        except ValueError as exc:raise ValueError('执行时间无效，小时0—23、分秒0—59') from exc
        config={**DEFAULTS,'enabled':enabled,'local_time':local_time,'updated_at':time.time()}
        with self.store.connect() as db:db.execute('UPDATE settings SET document=? WHERE name=?',(json.dumps(config),'auto_training'))
        self.apply_schedule();return self.get()
    def get(self):
        config=self.config();next_run=self.trigger(config).get_next_fire_time(None,datetime.now(ZONE)) if config['enabled'] else None
        with self.store.connect() as db:rows=db.execute('SELECT document FROM daily_runs ORDER BY rowid DESC LIMIT 100').fetchall()
        runs=[]
        for (raw,) in rows:
            record=json.loads(raw)
            if record.get('job_id'):
                try:
                    job=self.store.get(record['job_id']);record['job_status']=job['status'];record['job_message']=job.get('error',job.get('message',''))
                except KeyError:record['job_message']='对应任务记录缺失'
            runs.append(record)
        return {**config,'next_run_time':next_run.isoformat() if next_run else None,'runs':runs}
    def save_run(self,record):
        with self.store.connect() as db:db.execute('UPDATE daily_runs SET document=? WHERE id=?',(json.dumps(record,ensure_ascii=False),record['id']))
        return record
    def check(self,source='manual_check'):
        if not self.lock.acquire(blocking=False):return {'status':'skipped','message':'已有新增数据检查正在执行'}
        record={'id':uuid4().hex,'source':source,'at':datetime.now(ZONE).isoformat(),'status':'checking','message':'正在检查正常新数据'}
        try:
            config=self.config()
            if source=='auto' and not config['enabled']:return {'status':'skipped','message':'每日任务已关闭'}
            slot=None
            if source=='auto':
                now=datetime.now(ZONE);h,m,s=map(int,config['local_time'].split(':'));due=now.replace(hour=h,minute=m,second=s,microsecond=0)
                if due>now:due-=timedelta(days=1)
                if (now-due).total_seconds()>1:return self.save_missed(due,now,'调度到达超过1秒，已错过计划')
                slot=due.date().isoformat()
            try:
                with self.store.connect() as db:db.execute('INSERT INTO daily_runs VALUES (?,?,?)',(record['id'],slot,json.dumps(record,ensure_ascii=False)))
            except sqlite3.IntegrityError:return {'status':'skipped','message':'今天的每日任务已检查，避免重复执行'}
            if any(j['status'] in ACTIVE for j in self.store.list()):
                record.update(status='skipped',message='已有手动/自动训练或候选验证在运行，本次跳过，正常新数据保留');return self.save_run(record)
            choices=[d for d in self.registry.list() if d['status']=='ready' and d.get('normal_attested') and not self.store.was_consumed(d)]
            if not choices:
                record.update(status='skipped',message='没有合格且未处理的新数据；重复上传或重复回放不算新增数据');return self.save_run(record)
            dataset=sorted(choices,key=lambda d:d['start_time'])[0]
            key='daily:'+str(slot)+':'+dataset['content_id'] if source=='auto' else 'manual-check:'+record['id']
            try:job=self.jobs.create(dataset['dataset_id'],key,source=source)
            except (ValueError,KeyError) as exc:
                record.update(status='skipped',message='未启动训练：'+str(exc));return self.save_run(record)
            record.update(status='dispatched',dataset_id=dataset['dataset_id'],content_id=dataset['content_id'],job_id=job['job_id'],message='已提交真实后训练，生成候选后须验证并手动激活')
            return self.save_run(record)
        except Exception as exc:
            record.update(status='failed',message='每日检查失败：'+str(exc));return self.save_run(record)
        finally:self.lock.release()
    def close(self):
        if self.scheduler:self.scheduler.shutdown(wait=False);self.scheduler=None
