import importlib.util
from types import SimpleNamespace

def test_negative_and_stop_controls_exist(qtbot):
    from sensor_demo.app import MainWindow
    w=MainWindow();qtbot.addWidget(w)
    assert hasattr(w,'inject_low'),'User cannot inject a low fault yet'
    assert hasattr(w,'stop_fault_button'),'User cannot stop an injection yet'
    assert hasattr(w,'history_button'),'User cannot open the event history yet'
    w.close()

def test_history_can_filter_channel_and_shows_latest_first(qtbot):
    assert importlib.util.find_spec('sensor_demo.history') is not None,'User event history is not implemented'
    from sensor_demo.history import EventHistoryDialog
    events=[{'channel':'a','start_seq':1,'alarm_seq':3,'source_time':'2025-01-01T00:00:03','residual':2.,'threshold':1.,'status':'active','model_version':'v1'}, {'channel':'b','start_seq':5,'alarm_seq':7,'source_time':'2025-01-01T00:00:07','residual':3.,'threshold':1.,'status':'recovered','recovery_source_time':'2025-01-01T00:00:12','model_version':'v1'}]
    d=EventHistoryDialog(lambda:events,['a','b']);qtbot.addWidget(d)
    assert d.table.item(0,0).text()=='b'
    d.channel_filter.setCurrentText('a');d.refresh()
    assert d.table.rowCount()==1 and d.table.item(0,0).text()=='a'
    d.close()

def test_twin_starts_without_fabricated_measurements(qtbot):
    from sensor_demo.app import MainWindow
    w=MainWindow();qtbot.addWidget(w)
    assert hasattr(w,'twin_view'),'Third page still contains only placeholder text'
    assert '未定位' in w.twin_view.caption.text()
    assert w.twin_view.values_table.rowCount()==0
    w.close()

def test_reset_clears_overview_alarm_status_and_summary(qtbot):
    from PyQt6.QtWidgets import QTableWidgetItem
    from sensor_demo.app import MainWindow
    from sensor_demo.alarms import AlarmEngine
    w=MainWindow();qtbot.addWidget(w)
    w.dataset=SimpleNamespace(channels=['TT010301','FT010006'],model_channels=['TT010301'])
    w.data_table.setRowCount(2)
    w.data_table.setItem(0,7,QTableWidgetItem('预警'))
    w.data_table.setItem(1,7,QTableWidgetItem('不作判断'))
    w.alarm_engine=AlarmEngine(['TT010301'],[1.])
    for seq in range(3):w.alarm_engine.update(seq,[5.],[0.])
    w.alarm_label.setText('活动预警 1')
    w.model_available=True;w.model_version='baseline'
    w.service_label.setText('正在真实预测 · 源时间暂缓')
    w.reset_replay()
    assert w.data_table.item(0,7).text()=='待预热'
    assert w.data_table.item(1,7).text()=='不作判断'
    assert '已有事件0条' in w.alarm_label.text()
    assert '等待播放' in w.service_label.text()
    assert not w.alarm_engine.events
    w.close()

def test_switching_model_does_not_erase_previous_version_events(qtbot):
    import json
    from sensor_demo.app import MainWindow
    from sensor_demo.config import ROOT
    from sensor_demo.alarms import AlarmEngine
    channels=json.loads((ROOT/'configs/model-schema.json').read_text(encoding='utf-8'))['model_channels']
    w=MainWindow();qtbot.addWidget(w);w.model_version='old';w.alarm_calibration_id='old-calibration'
    w.alarm_engine=AlarmEngine(channels,[1.]*len(channels))
    values=[0.]*len(channels);values[0]=5.
    for seq in range(3):w.alarm_engine.update(seq,values,[0.]*len(channels))
    w.alarm_engine.events[0]['model_version']='old'
    w.on_service_result('spec',{'model_version':'new','schema_id':'test','input_channel_ids':channels,'input_length':96,'prediction_length':96,'alarm_calibration':{'model_version':'new','channels':channels,'thresholds':[1.]*len(channels),'calibration_id':'new-calibration'}})
    w.show_history();events=w.history_dialog.filtered()
    assert len(events)==1 and events[0]['model_version']=='old'
    assert events[0]['status']=='ended' and '模型' in events[0]['end_reason']
    assert not w.alarm_engine.active
    w.reset_replay();assert not w.event_history()
    w.close()

def test_paused_views_immediately_show_prediction_unavailable(qtbot):
    import numpy as np
    from datetime import datetime
    from sensor_demo.app import MainWindow
    from sensor_demo.alarms import AlarmEngine
    tags=['SCRDLFK','SCRDYFK','avg','max','AT010301','AT010302','TT010301','TT010302','TT010303','PT010303','PT010304','FT010401']
    w=MainWindow();qtbot.addWidget(w)
    w.dataset=SimpleNamespace(channels=tags,model_channels=tags,model_indices=list(range(len(tags))),units=['raw']*len(tags),times=[datetime(2025,1,1)])
    w.clock.seq=0;w.observations=[np.zeros(len(tags))];w.data_table.setRowCount(len(tags))
    w.alarm_engine=AlarmEngine(tags,[1.]*len(tags));w.alarm_engine.update(0,np.zeros(len(tags)),np.zeros(len(tags)))
    from PyQt6.QtWidgets import QTableWidgetItem
    for row in range(len(tags)):w.data_table.setItem(row,7,QTableWidgetItem('正常'))
    w.twin_view.update_frame(w.dataset,w.observations[-1],w.alarm_engine,0)
    w.invalidate_forecasts()
    assert w.data_table.item(0,7).text()=='不可用'
    assert '不可用' in w.twin_view.values_table.item(0,2).text()
    w.close()
