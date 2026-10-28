"""Prepare an audited public source tree, excluding developer data and credentials."""
from __future__ import annotations
import argparse
import base64
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
    parser.add_argument('--large-file',type=int)
    parser.add_argument('--part',type=int,default=0)
    parser.add_argument('--part-size',type=int,default=24000)
    parser.add_argument('--remote-tree',type=Path)
    parser.add_argument('--smoke-root',type=Path)
    args=parser.parse_args()
    output=ROOT/'.plcforge/github-upload.json'
    if args.large_file is not None:
        payload=json.loads(output.read_text(encoding='utf-8'))
        encoded=payload['large_files'][args.large_file]['base64']
        sys.stdout.write(encoded[args.part*args.part_size:(args.part+1)*args.part_size])
        return
    if args.chunk is not None:
        payload=json.loads(output.read_text(encoding='utf-8'))
        print(json.dumps(payload['chunks'][args.chunk],ensure_ascii=True))
        return
    load_env(ROOT/'.env')
    files=[ROOT/name for name in ('README.md','LICENSE','THIRD_PARTY_NOTICES.md','package.json','pyproject.toml',
                                  '.gitignore','.gitattributes','.env.example','settings.example.json','plcforge.cmd')]
    for directory,extensions in [('plcforge',{'.py','.md'}),('bin',{'.cjs'}),('npm',{'.cjs','.json'}),
                                 ('tests',{'.py','.cjs'}),('data/tasks',{'.json'}),
                                 ('examples/reference_solutions',{'.st'}),('experiments',{'.json'})]:
        files += [p for p in (ROOT/directory).rglob('*') if p.is_file() and p.suffix in extensions and '__pycache__' not in p.parts]
    files += [ROOT/'docs'/name for name in ('LLM_SETTINGS.md','NPM_INSTALL.md','TERMINAL_AND_TRANSCRIPTS.md')]
    files += [ROOT/'docs/AGENT_IMPROVEMENTS_0.1.5.md',ROOT/'docs/validation/agent-v0.1.5.json',ROOT/'.github/workflows/tests.yml']
    files += list((ROOT/'evaluation').rglob('*.json'))
    files += [ROOT/'scripts'/name for name in ('launch.ps1','setup.ps1','install-global.ps1','prepare_github.py',
        'build_p01_acceptance.py','run_p01_acceptance.py','summarize_p01_acceptance.py',
        'verify_agent_native.py','build_acceptance_report.py','codesys_batch_build.py','verify_npm_upgrade.py')]
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
    if args.smoke_root:
        smoke_root=args.smoke_root.resolve()
        if not smoke_root.is_relative_to((ROOT/'.plcforge').resolve()):
            raise ValueError('Public source smoke directory must be within .plcforge')
        for entry in entries:
            dest=smoke_root/entry['path']
            dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_text(entry['content'],encoding='utf-8',newline='\n')
    all_count=len(entries)
    if args.remote_tree:
        remote=json.loads(args.remote_tree.read_text(encoding='utf-8'))
        known={item['path']:item['sha'] for item in remote['tree'] if item['type']=='blob'}
        def blob_sha(entry):
            raw=entry['content'].encode('utf-8')
            return hashlib.sha1(b'blob '+str(len(raw)).encode('ascii')+b'\x00'+raw).hexdigest()
        entries=[entry for entry in entries if known.get(entry['path'])!=blob_sha(entry)]
    large_files=[]
    small_entries=[]
    for entry in entries:
        if len(entry['content'].encode('utf-8')) > 90000:
            encoded=base64.b64encode(entry['content'].encode('utf-8')).decode('ascii')
            large_files.append({'path':entry['path'],'mode':entry['mode'],'base64':encoded,
                                'parts':(len(encoded)+args.part_size-1)//args.part_size})
        else:
            small_entries.append(entry)
    chunks=[]
    batch=[]
    size=0
    for entry in small_entries:
        serialized=len(json.dumps(entry,ensure_ascii=True))
        if batch and size+serialized>26000:
            chunks.append(batch)
            batch=[]
            size=0
        batch.append(entry)
        size+=serialized
    if batch: chunks.append(batch)
    write_json(output,{'chunks':chunks,'manifest':manifest,'large_files':large_files})
    write_json(ROOT/'evidence/github_source_manifest.json',{'files':manifest,'credential_scan':'passed',
        'excluded':['credentials','machine settings','raw sessions','vendor binaries','original assignment','upstream reference source']})
    print(json.dumps({'files':all_count,'changed_files':len(entries),'chunks':len(chunks),'large_files':[{'path':f['path'],'parts':f['parts']} for f in large_files],
                      'output':str(output),'credential_scan':'passed'}))


if __name__=='__main__':
    main()
