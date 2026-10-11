"""Conservative local publication check; never uploads or modifies files."""
import argparse
from pathlib import Path
import re
import subprocess
import zipfile

FORBIDDEN_PARTS={'data','logs','exports','reports','runtime','.venv','inventory','fixtures'}
FORBIDDEN_SUFFIXES={'.cu8','.iq','.wav','.mp3','.sqlite','.db','.duckdb','.parquet','.csv','.log','.sigmf-data','.sigmf-meta','.npz'}
MARKERS=[r'C:\\Users\\',r'C:\\SDRTools\\',r'C:\\RTLSDRBlog\\',r'00000001',r'451[._]300',r'ghp_[A-Za-z0-9]{20,}',r'sk-[A-Za-z0-9]{20,}']


def violations(name,content):
    path=Path(name);result=[]
    if set(path.parts)&FORBIDDEN_PARTS or path.suffix.lower() in FORBIDDEN_SUFFIXES or '.duckdb.' in path.name.lower() or name.endswith('config.local.toml'):
        result.append(f'{name}: private/runtime path')
    text=content.decode('utf8',errors='replace')
    for pattern in MARKERS:
        if re.search(pattern,text): result.append(f'{name}: private identifier/path or credential pattern')
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--tracked',action='store_true');parser.add_argument('--wheel',type=Path);args=parser.parse_args()
    root=Path(__file__).resolve().parents[1];errors=[];count=0
    if args.wheel:
        with zipfile.ZipFile(args.wheel) as archive:
            for name in archive.namelist():
                if name.endswith('/'):continue
                count+=1;errors+=violations(name,archive.read(name))
    else:
        names=subprocess.check_output(['git','ls-files','-z'],cwd=root).decode().split('\0')
        for name in filter(None,names):
            count+=1
            if name=='tools/release_check.py':continue  # detector patterns themselves
            content=subprocess.check_output(['git','show',':'+name],cwd=root)
            errors+=violations(name,content)
    for error in errors:print(error)
    print(f'Checked {count} files; {len(errors)} publication findings. Manual review is still required.')
    return bool(errors)


if __name__=='__main__':raise SystemExit(main())
