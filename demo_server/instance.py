from pathlib import Path
from filelock import FileLock

class BackendInstance:
    """One backend per mutable data directory, including across HTTP ports."""
    def __init__(self,root):
        root=Path(root);root.mkdir(parents=True,exist_ok=True)
        self.lock=FileLock(root/'backend.lock',timeout=0)
    def __enter__(self):self.lock.acquire();return self
    def __exit__(self,*args):self.lock.release()
