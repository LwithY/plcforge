"""Prepare an audited public source tree, excluding developer data and credentials."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from plcforge.config import load_env
from plcforge.security import redact, write_json


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--chunk',type=int)
    args=parser.parse_args()
    output=ROOT/'.plcforge/github-upload.json'
    if args.chunk is not None:
        payload=json.loads(output.read_text(encoding='utf-8'))
        print(json.dumps(payload['chunks'][args.chunk],ensure_ascii=True))
        return
    load_env(ROOT/'.env')
    files=[ROOT/name for name in ('README.md','LICENSE','THIRD_PARTY_NOTICES.md','package.json','pyproject.toml',
                                  '.gitignore','.env.example','settings.example.json','plcforge.cmd')]
    for directory,extensions in [('plcforge',{'.py','.md'}),('bin',{'.cjs'}),('npm',{'.cjs','.json'}),
                                 ('tests',{'.py','.cjs'}),('data/tasks',{'.json'}),
                                 ('examples/reference_solutions',{'.st'}),('experiments',{'.json'})]:
        files += [p for p in (ROOT/directory).rglob('*') if p.is_file() and p.suffix in extensions and '__pycache__' not in p.parts]
    files += [ROOT/'docs'/name for name in ('LLM_SETTINGS.md','NPM_INSTALL.md','TERMINAL_AND_TRANSCRIPTS.md')]
    files += [ROOT/'scripts'/name for name in ('launch.ps1','setup.ps1','install-global.ps1','prepare_github.py')]
    entries=[]
    manifest=[]
    for file in sorted(set(files)):
        relative=file.relative_to(ROOT).as_posix()
        if not file.is_file():
            raise ValueError('Missing public source: '+relative)
        content=file.read_text(encoding='utf-8-sig')
        if redact(content)!=content:
            raise ValueError('Credential-like data in '+relative)
        mode='100755' if relative=='bin/plcforge.cjs' else '100644'
        entries.append({'path':relative,'mode':mode,'type':'blob','content':content})
        manifest.append({'path':relative,'sha256':hashlib.sha256(content.encode('utf-8')).hexdigest()})
    chunks=[]
    batch=[]
    size=0
    for entry in entries:
        serialized=len(json.dumps(entry,ensure_ascii=True))
        if batch and size+serialized>26000:
            chunks.append(batch)
            batch=[]
            size=0
        batch.append(entry)
        size+=serialized
    if batch: chunks.append(batch)
    write_json(output,{'chunks':chunks,'manifest':manifest})
    write_json(ROOT/'evidence/github_source_manifest.json',{'files':manifest,'credential_scan':'passed',
        'excluded':['credentials','machine settings','raw sessions','vendor binaries','original assignment','upstream reference source']})
    print(json.dumps({'files':len(entries),'chunks':len(chunks),'output':str(output),'credential_scan':'passed'}))


if __name__=='__main__':
    main()
