from types import SimpleNamespace
from pathlib import Path
import hashlib,sys
from .contracts import CHANNELS
from .model import MODEL_ROOT

def load_bundle_model(path,expected_sha256):
    import torch
    path=Path(path)
    if hashlib.sha256(path.read_bytes()).hexdigest()!=expected_sha256:raise ValueError('模型包SHA256不一致，拒绝加载')
    bundle=torch.load(path,map_location='cpu',weights_only=True)
    if bundle['format_version']!=1 or bundle['dataset']!='industrial' or bundle['schema']['channels']!=CHANNELS or bundle['graph']['channels']!=CHANNELS:raise ValueError('模型包通道或格式不匹配')
    if (bundle['schema']['input_length'],bundle['schema']['output_length'])!=(96,96):raise ValueError('模型包窗口不匹配')
    sys.path.insert(0,str(MODEL_ROOT))
    from scripts.model_factory import configure_imports
    configure_imports('industrial')
    from relation_model import PhysicsRelationCTF
    model=PhysicsRelationCTF(SimpleNamespace(**bundle['config']),bundle['graph'],bundle['scalers'],**bundle['options'])
    model.load_state_dict(bundle['state_dict'],strict=True)
    return bundle,model
