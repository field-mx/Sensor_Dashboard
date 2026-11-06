import importlib.util
import numpy as np
import pytest

def components():
    assert importlib.util.find_spec('demo_server') is not None,'Real model service is not implemented'
    from sensor_demo.predictions import PredictionLedger
    return PredictionLedger

def test_ledger_rejects_old_generation_and_future_cutoff():
    Ledger=components();l=Ledger();l.reset('run',2,'baseline')
    response={'run_id':'run','generation':1,'model_version':'baseline','cutoff_seq':95,'target_seqs':list(range(96,192)),'predictions':np.ones((96,56)).tolist()}
    assert not l.add(response)
    response['generation']=2;assert l.add(response)
    assert l.for_target(95) is None
    assert np.asarray(l.for_target(96)).shape==(56,)
    l.reset('run',3,'baseline');assert l.for_target(96) is None

def test_request_validation_prevents_wrong_time_and_shape():
    components()
    from demo_server.contracts import PredictRequest
    payload={'run_id':'r','generation':1,'request_id':'q','model_version':'baseline','schema_id':'prctf-industrial-56-v1','cutoff_seq':95,'source_times':[f'2025-01-01T00:{i//60:02}:{i%60:02}' for i in range(96)],'channel_ids':['wrong']*56,'history':np.zeros((96,56)).tolist()}
    with pytest.raises(ValueError):PredictRequest(**payload)

def test_local_backend_address_is_readable(qtbot):
    from sensor_demo.app import MainWindow
    from sensor_demo.styles import DARK_STYLE
    w=MainWindow();w.setStyleSheet(DARK_STYLE);qtbot.addWidget(w);w.show();qtbot.waitExposed(w)
    required=w.api_edit.fontMetrics().horizontalAdvance(w.api_edit.text())+18
    assert w.api_edit.width()>=required,'Local backend address is clipped in the actual window'
    w.close()

def test_plot_labels_have_valid_font_sizes(qtbot):
    from PyQt6.QtCore import qInstallMessageHandler
    from sensor_demo.app import MainWindow
    from sensor_demo.styles import DARK_STYLE
    messages=[]
    previous=qInstallMessageHandler(lambda kind,context,text:messages.append(text))
    try:
        w=MainWindow();w.setStyleSheet(DARK_STYLE);qtbot.addWidget(w);w.show();qtbot.waitExposed(w)
        assert not any('Point size <= 0' in text for text in messages),messages
        w.close()
    finally:qInstallMessageHandler(previous)
