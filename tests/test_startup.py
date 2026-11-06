import importlib.util
import subprocess
import sys

def test_module_entrypoint_exists():
    assert importlib.util.find_spec('sensor_demo.__main__') is not None, 'python -m sensor_demo requires __main__.py'

def test_client_startup_and_pages(qtbot):
    assert importlib.util.find_spec('sensor_demo') is not None, 'Demo entry point is not implemented'
    from sensor_demo.app import MainWindow
    window=MainWindow()
    qtbot.addWidget(window)
    window.show()
    assert window.windowTitle()=='电解槽AI监控系统'
    assert window.pages.count()==3
    for i in range(3):
        window.nav.setCurrentRow(i)
        assert window.pages.currentIndex()==i
    window.close()

def test_client_import_does_not_load_torch():
    assert importlib.util.find_spec('sensor_demo') is not None, 'Demo entry point is not implemented'
    code='import sensor_demo.app,sys; assert "torch" not in sys.modules'
    result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
