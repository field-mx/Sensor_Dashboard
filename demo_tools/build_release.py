"""Copy an auditable delivery folder, excluding developer environments and experiments."""
import argparse,hashlib,json,shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SKIP={'.git','.venv','__pycache__','.pytest_cache','runtime','exports','logs','results','runs','figures','recovery_conflicts','dist'}

def include(path):
    relative=path.relative_to(ROOT)
    if any(part in SKIP or 'SFConflict' in part for part in relative.parts):return False
    if path.suffix.lower() in ('.pyc','.log','.tmp'):return False
    if relative.parts[:3]==('vendor','PhysicsRelationCTF','weights'):
        return len(relative.parts)>=4 and relative.parts[3] in ('industrial','manifest.json')
    return True

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'dist/Sensor_Dashboard_Demo_1.0.0');args=parser.parse_args()
    target=args.output.resolve()
    if target.exists():raise SystemExit('Output already exists; choose a new folder to preserve previous deliveries.')
    if target==ROOT or ROOT.is_relative_to(target):raise SystemExit('Output must be an independent new folder.')
    target.mkdir(parents=True);manifest={}
    roots=['sensor_demo','demo_server','demo_tools','scripts','configs','docs','evidence','tests','vendor','sample_data','wheelhouse']
    files=[p for p in ROOT.iterdir() if p.is_file() and p.suffix.lower() in ('.md','.txt','.toml','.cmd')]
    for name in roots:
        folder=ROOT/name
        if folder.is_dir():files.extend(p for p in folder.rglob('*') if p.is_file())
    for source in sorted(files):
        if not include(source):continue
        relative=source.relative_to(ROOT);destination=target/relative;destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,destination);manifest[relative.as_posix()]={'bytes':destination.stat().st_size,'sha256':hashlib.sha256(destination.read_bytes()).hexdigest()}
    (target/'delivery-manifest.json').write_text(json.dumps({'version':'1.0.0','files':manifest},ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'Created {target} ; {len(manifest)} files ; {sum(f["bytes"] for f in manifest.values())/1024**2:.1f} MiB')

if __name__=='__main__':main()
