def test_editing_the_file_path_invalidates_previous_admission(qtbot,tmp_path):
    from sensor_demo.model_update import ModelUpdateDialog
    dialog=ModelUpdateDialog('http://127.0.0.1:1',tmp_path/'normal.xlsx');qtbot.addWidget(dialog)
    dialog.dataset_id='previous';dialog.report={'status':'validated'}
    dialog.normal_check.setChecked(True);dialog.update_controls()
    assert dialog.register_button.isEnabled()
    qtbot.keyClicks(dialog.path_edit,'x')
    assert not dialog.register_button.isEnabled(), 'Editing a filename must not retain approval for the previous file'
    assert dialog.dataset_id is None
    dialog.close()

def test_daily_settings_success_replaces_loading_and_connection_error(qtbot,tmp_path):
    from sensor_demo.model_update import ModelUpdateDialog
    dialog=ModelUpdateDialog('http://127.0.0.1:1',tmp_path/'normal.xlsx');qtbot.addWidget(dialog)
    config={'enabled':False,'local_time':'02:00:00','next_run_time':None,'runs':[]}
    dialog.on_result('autoconfig',config)
    assert '尚未读取' not in dialog.auto_status.text()
    dialog.on_error('autoconfig','Connection refused')
    dialog.on_result('autoconfig',config)
    assert '失败' not in dialog.auto_status.text()
    dialog.close()

def test_reimport_can_resume_an_existing_unregistered_dataset(qtbot,tmp_path,monkeypatch):
    import json
    from sensor_demo.config import ROOT
    from sensor_demo.model_update import ModelUpdateDialog
    dialog=ModelUpdateDialog('http://127.0.0.1:1',tmp_path/'normal.xlsx');qtbot.addWidget(dialog)
    calls=[];monkeypatch.setattr(dialog.client,'request',lambda method,path,payload=None,tag='request':calls.append((method,path,tag)))
    original=next(d for d in json.loads((ROOT/'evidence/training-admission.json').read_text(encoding='utf-8'))['datasets'] if d['status']=='ready')
    duplicate={'dataset_id':'duplicate','status':'duplicate','duplicate_of':original['dataset_id'],'error':'重复数据'}
    dialog.on_result('dataset',duplicate)
    assert ('GET','/api/v1/datasets/'+original['dataset_id'],'dataset') in calls
    original.update(status='validated',normal_attested=False)
    dialog.on_result('dataset',original)
    assert not dialog.register_button.isEnabled()
    dialog.normal_check.setChecked(True)
    assert dialog.register_button.isEnabled() and dialog.dataset_id==original['dataset_id']
    dialog.close()
