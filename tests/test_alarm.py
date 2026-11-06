import importlib.util
import numpy as np

def components():
    assert importlib.util.find_spec('sensor_demo.alarms') is not None,'Residual alarm engine is not implemented'
    from sensor_demo.alarms import AlarmEngine,FaultInjector
    return AlarmEngine,FaultInjector

def test_fault_changes_copy_and_alarm_uses_residual_only():
    Alarm,Fault=components();source=np.array([10.,20.]);injector=Fault()
    injector.start(channel=0,amplitude=8,duration=5,start_seq=10)
    observed=injector.apply(10,source)
    assert source.tolist()==[10.,20.] and observed.tolist()==[18.,20.]
    engine=Alarm(['a','b'],np.array([2.,2.]))
    for seq in range(2):engine.update(seq,[10.,20.],[10.,20.])
    assert not engine.events
    for seq in range(10,13):engine.update(seq,injector.apply(seq,source),[10.,20.])
    assert len(engine.events)==1 and engine.events[0]['channel']=='a'
    assert engine.events[0]['alarm_seq']==12

def test_prediction_unavailable_is_not_normal_and_reset_clears():
    Alarm,Fault=components();engine=Alarm(['a'],np.array([1.]))
    engine.update(1,[20.],None)
    assert engine.statuses==['unavailable'] and not engine.events
    engine.reset();assert not engine.events and engine.statuses==['unavailable']

def test_active_alarm_recovers_after_five_valid_normal_points():
    Alarm,Fault=components();engine=Alarm(['a'],np.array([1.]))
    for seq in range(3):engine.update(seq,[10.],[0.])
    assert engine.statuses==['alarm']
    for seq in range(3,7):engine.update(seq,[0.],[0.])
    assert engine.statuses==['alarm']
    engine.update(7,[0.],[0.])
    assert engine.statuses==['normal'] and not engine.active
    assert engine.events[0]['status']=='recovered' and engine.events[0]['recovery_seq']==7

def test_connection_recovery_keeps_event_history(qtbot):
    import json
    from sensor_demo.app import MainWindow
    from sensor_demo.config import ROOT
    calibration=json.loads((ROOT/'configs/baseline-alarm.json').read_text(encoding='utf-8'))
    spec={'input_channel_ids':calibration['channels'],'input_length':96,'prediction_length':96,'model_version':calibration['model_version'],'alarm_calibration':calibration}
    w=MainWindow();qtbot.addWidget(w);w.on_service_result('spec',spec)
    for seq in range(3):w.alarm_engine.update(seq,np.asarray(calibration['thresholds'])*2,np.zeros(56))
    event_id=w.alarm_engine.events[0]['id']
    w.on_service_error('health','Connection refused');w.on_service_result('spec',spec)
    assert w.alarm_engine.events and w.alarm_engine.events[0]['id']==event_id
