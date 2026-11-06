"""Install only into this release's isolated Python environment."""
import argparse,os,struct,subprocess,sys
from pathlib import Path

def run(command,cwd):
    result=subprocess.run(list(map(str,command)),cwd=cwd)
    if result.returncode:raise RuntimeError('Installation step failed (exit '+str(result.returncode)+'). Retry Setup.cmd with the output available.')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--environment-directory');args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    try:
        if sys.version_info[:2]!=(3,11) or struct.calcsize('P')!=8:raise RuntimeError('Install 64-bit Python 3.11 first.')
        target=Path(args.environment_directory).resolve() if args.environment_directory else root/'.venv'
        python=target/'Scripts/python.exe'
        if not python.is_file():run([sys.executable,'-m','venv',target],root)
        if (root/'wheelhouse').is_dir():
            run([python,'-m','pip','install','--no-index','--find-links',root/'wheelhouse','-r',root/'requirements-lock.txt'],root)
        else:
            run([python,'-m','pip','install','torch==2.11.0+cpu','--index-url','https://download.pytorch.org/whl/cpu'],root)
            run([python,'-m','pip','install','-r',root/'requirements-lock.txt'],root)
        run([python,'-m','pip','check'],root);run([python,'-m','demo_tools.doctor'],root)
        print('Setup complete. Double-click StartDemo.cmd.');return 0
    except (OSError,RuntimeError) as exc:print(str(exc),file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
