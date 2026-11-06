from pathlib import Path
import json,os
ROOT=Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME=Path(os.environ['LOCALAPPDATA'])/'SensorDashboardDemo/runtime' if os.name=='nt' and os.environ.get('LOCALAPPDATA') else ROOT/'runtime'
RUNTIME=Path(os.environ.get('SENSOR_DEMO_DATA_DIR',str(DEFAULT_RUNTIME))).expanduser().resolve()
DEFAULT_DATA=ROOT/'sample_data/SensorData.xlsx'
SETTINGS=RUNTIME/'client.json'

def load_settings():
    defaults={'api_url':'http://127.0.0.1:18000','data_path':str(DEFAULT_DATA)}
    if SETTINGS.exists():
        try: defaults.update(json.loads(SETTINGS.read_text(encoding='utf-8')))
        except (OSError,ValueError): pass
    path=Path(defaults['data_path']).expanduser()
    if not path.is_absolute():path=ROOT/path
    defaults['data_path']=str(path if path.is_file() else DEFAULT_DATA)
    return defaults

def save_settings(values):
    RUNTIME.mkdir(parents=True,exist_ok=True)
    tmp=SETTINGS.with_suffix('.tmp')
    tmp.write_text(json.dumps(values,ensure_ascii=False,indent=2),encoding='utf-8')
    tmp.replace(SETTINGS)
