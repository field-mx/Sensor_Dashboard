from types import SimpleNamespace
from pathlib import Path
import importlib.util
import pytest

def service_pair(tmp_path):
    from demo_server.jobs import JobService
    from demo_server.scheduling import ScheduleService
    row={'dataset_id':'normal','status':'ready','normal_attested':True,'content_id':'c'*64,'start_time':'2025-01-01T00:00:00','end_time':'2025-01-01T01:00:00','partitions':[{'points':768},{'points':256},{'points':256}]}
    registry=SimpleNamespace(root=tmp_path,list=lambda:[row],get=lambda key:row)
    models=SimpleNamespace(version='baseline',bundle_path=tmp_path/'base.pt',bundle_sha256='sha',busy=False)
    jobs=JobService(registry,models,autostart=False)
    return ScheduleService(jobs,autostart=False),jobs,row

def test_daily_config_defaults_persists_and_rejects_invalid_time(tmp_path):
    assert importlib.util.find_spec('demo_server.scheduling'), 'Daily scheduling is not implemented'
    schedule,jobs,_=service_pair(tmp_path)
    try:
        assert schedule.get()['enabled'] is False and schedule.get()['local_time']=='02:00:00'
        assert schedule.set_config(True,'08:45:10')['next_run_time'].endswith('+08:00')
        with pytest.raises(ValueError):schedule.set_config(True,'25:00')
        schedule.close()
        from demo_server.scheduling import ScheduleService
        restored=ScheduleService(jobs,autostart=False)
        assert restored.get()['enabled'] and restored.get()['local_time']=='08:45:10'
        restored.close()
    finally:jobs.close()

def test_daily_check_skips_busy_and_processed_data(tmp_path):
    assert importlib.util.find_spec('demo_server.scheduling'), 'Daily scheduling is not implemented'
    schedule,jobs,row=service_pair(tmp_path)
    try:
        first=schedule.check('manual_check')
        assert first['status']=='dispatched'
        assert schedule.check('manual_check')['status']=='skipped'
        jobs.cancel(first['job_id'])
        jobs.store.mark_consumed(jobs.store.get(first['job_id']),row)
        again=schedule.check('manual_check')
        assert again['status']=='skipped' and '新数据' in again['message']
        assert len(jobs.store.list())==1
    finally:schedule.close();jobs.close()

def test_startup_records_missed_schedule_without_catchup_training(tmp_path):
    from datetime import datetime,timedelta
    from demo_server.scheduling import ZONE
    schedule,jobs,_=service_pair(tmp_path)
    try:
        schedule.set_config(True,'00:00:00')
        now=datetime.now(ZONE)+timedelta(days=1)
        schedule.record_missed(now);schedule.record_missed(now)
        records=schedule.get()['runs']
        assert len(records)==1 and records[0]['status']=='missed'
        assert jobs.store.list()==[]
    finally:schedule.close();jobs.close()

def test_running_scheduler_persists_missed_execution_without_catchup(tmp_path):
    from datetime import datetime,timedelta
    import threading,time
    from apscheduler.executors.pool import ThreadPoolExecutor
    from demo_server.scheduling import ScheduleService,ZONE
    old,jobs,_=service_pair(tmp_path);old.close()
    schedule=ScheduleService(jobs);release=threading.Event();started=threading.Event()
    def block():started.set();release.wait(10)
    try:
        schedule.scheduler.add_executor(ThreadPoolExecutor(1),alias='blocked-test')
        schedule.scheduler.add_job(block,'date',run_date=datetime.now(ZONE),executor='blocked-test')
        assert started.wait(2)
        schedule.set_config(True,(datetime.now(ZONE)+timedelta(seconds=2)).strftime('%H:%M:%S'))
        schedule.scheduler.modify_job('daily-training',executor='blocked-test')
        time.sleep(3.2);release.set()
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            records=schedule.get()['runs']
            if records and records[0]['status']!='checking':break
            time.sleep(.02)
        assert schedule.get()['runs'][0]['status']=='missed'
        assert jobs.store.list()==[]
    finally:release.set();schedule.close();jobs.close()
