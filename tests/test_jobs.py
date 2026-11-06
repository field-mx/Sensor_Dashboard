from types import SimpleNamespace
import importlib.util
import pytest

def test_training_job_is_idempotent_and_cannot_overlap(tmp_path):
    assert importlib.util.find_spec('demo_server.jobs'), 'Persistent training jobs are not implemented'
    from demo_server.jobs import JobService
    data={'dataset_id':'normal','status':'ready','normal_attested':True,'content_id':'c','partitions':[{'points':768},{'points':256},{'points':256}]}
    registry=SimpleNamespace(root=tmp_path,get=lambda key:data)
    models=SimpleNamespace(version='baseline',bundle_path=tmp_path/'baseline.pt',bundle_sha256='sha',busy=False)
    service=JobService(registry,models,autostart=False)
    try:
        first=service.create('normal','key1')
        assert service.create('normal','key1')['job_id']==first['job_id']
        with pytest.raises(ValueError,match='进行|运行|训练'):
            service.create('normal','key2')
        cancelled=service.cancel(first['job_id'])
        assert cancelled['status']=='cancelled'
        assert service.create('normal','key2')['job_id']!=first['job_id']
    finally:service.close()

def test_invalid_dataset_never_starts_training(tmp_path):
    assert importlib.util.find_spec('demo_server.jobs'), 'Persistent training jobs are not implemented'
    from demo_server.jobs import JobService
    registry=SimpleNamespace(root=tmp_path,get=lambda key:{'status':'rejected','normal_attested':False})
    models=SimpleNamespace(version='baseline',bundle_path=tmp_path/'baseline.pt',bundle_sha256='sha',busy=False)
    service=JobService(registry,models,autostart=False)
    try:
        with pytest.raises(ValueError,match='正常|登记'):service.create('invalid','key')
        assert not service.store.list()
    finally:service.close()

def test_legacy_ready_dataset_with_short_evaluation_is_rejected_before_training(tmp_path):
    from demo_server.jobs import JobService
    data={'dataset_id':'old','status':'ready','normal_attested':True,'content_id':'c','partitions':[{'name':'训练','points':669},{'name':'验证','points':223},{'name':'评估','points':223}]}
    registry=SimpleNamespace(root=tmp_path,get=lambda key:data)
    models=SimpleNamespace(version='baseline',bundle_path=tmp_path/'baseline.pt',bundle_sha256='sha',busy=False)
    service=JobService(registry,models,autostart=False)
    try:
        with pytest.raises(ValueError,match='256|故障回归'):service.create('old','key')
        assert not service.store.list()
    finally:service.close()
