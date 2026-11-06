import hashlib,json,sys
from pathlib import Path
from importlib.metadata import version
from sensor_demo.config import ROOT,RUNTIME,DEFAULT_DATA

def main():
    try:
        assert sys.version_info[:2]==(3,11),'需要Python3.11'
        for line in (ROOT/'requirements-lock.txt').read_text(encoding='utf-8-sig').splitlines():
            if not line.strip():continue
            name,expected=line.strip().split('==');assert version(name)==expected,f'{name}版本不匹配：需要{expected}，实际{version(name)}'
        model=ROOT/'vendor/PhysicsRelationCTF';manifest=json.loads((model/'weights/manifest.json').read_text())
        entry=manifest['models']['industrial'];weight=model/entry['path']
        assert hashlib.sha256(weight.read_bytes()).hexdigest()==entry['sha256'],'公开工业权重哈希不一致'
        upstream=json.loads((model/'manifests/ctf_upstream.json').read_text())
        for name,sha in upstream['files'].items():assert hashlib.sha256((model/'workspace/CTF/ChannelTokenFormer'/name).read_bytes()).hexdigest()==sha,'CTF源码校验失败：'+name
        assert hashlib.sha256(DEFAULT_DATA.read_bytes()).hexdigest()=='2cd55e2562f822800e9b363e9a03b1f39579f17656edaf62e5d619f8f5345d61','随包Excel缺失或改变'
        release=ROOT/'delivery-manifest.json'
        if release.is_file():
            for name,entry in json.loads(release.read_text(encoding='utf-8'))['files'].items():
                path=(ROOT/name).resolve()
                assert path.is_relative_to(ROOT) and path.is_file(), '交付文件缺失：'+name
                assert path.stat().st_size==entry['bytes'] and hashlib.sha256(path.read_bytes()).hexdigest()==entry['sha256'],'交付文件校验失败：'+name
        RUNTIME.mkdir(parents=True,exist_ok=True)
        if '--model' in sys.argv:
            from demo_server.model import ModelService
            import numpy as np
            service=ModelService();raw=np.broadcast_to(service.predictor.mean.cpu().numpy(),(96,56)).copy()
            prediction=service.predictor.predict(raw)
            assert prediction.shape==(96,56) and np.isfinite(prediction).all(),'真实模型输出契约错误'
        print('通过：固定依赖、工业权重、CTF上游、样例Excel完整。')
        print('运行数据目录：'+str(RUNTIME));return 0
    except Exception as exc:print('检查未通过：'+str(exc),file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
