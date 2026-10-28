"""Exercise real npm replacement in one isolated global prefix; no user config is touched."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import uuid

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline',type=Path,default=ROOT/'dist/plcforge-0.1.4.tgz')
    parser.add_argument('--current',type=Path,required=True)
    parser.add_argument('--python',type=Path,default=Path(sys.executable))
    parser.add_argument('--registry',action='store_true',help='Install the second version from the official npm latest tag')
    args = parser.parse_args()
    node = Path(shutil.which('node') or '')
    npm = node.parent/'node_modules/npm/bin/npm-cli.js'
    if not node.is_file() or not npm.is_file():
        raise ValueError('Node.js and its adjacent npm CLI are required')
    run = ROOT/'.plcforge/npm-upgrade'/('upgrade-'+uuid.uuid4().hex[:10])
    prefix, home, project = run/'prefix', run/'home', run/'outside-project'
    for directory in (prefix,home,project):
        directory.mkdir(parents=True,exist_ok=False)
    env = dict(os.environ,PLCFORGE_HOME=str(home.resolve()),PLCFORGE_PYTHON=str(args.python.resolve()),
               PLCFORGE_SKIP_CODESYS_SETUP='1',PLCFORGE_CONFIG=str(home/'plcforge.json'),
               npm_config_cache=str(run/'cache'),PYTHONIOENCODING='utf-8',PYTHONUTF8='1')

    def execute(command, label):
        result = subprocess.run(command,cwd=project,env=env,text=True,encoding='utf-8',errors='replace',
                                stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=120,
                                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        (run/(label+'.log')).write_text(result.stdout,encoding='utf-8')
        if result.returncode:
            raise ValueError(label+' failed; see '+str(run/(label+'.log')))
        return result.stdout

    def install(archive, label, from_registry=False):
        target = 'plcforge@latest' if from_registry else str(archive.resolve())
        flags = ['--registry=https://registry.npmjs.org/','--prefer-online'] if from_registry else ['--offline']
        return execute([str(node),str(npm),'install','--global','--prefix',str(prefix),target,
                        '--no-audit','--no-fund','--foreground-scripts']+flags,label)

    def cli(arguments, label):
        shim = prefix/'plcforge.cmd'
        if not shim.is_file():
            raise ValueError('npm did not create its global command shim')
        # Paths and arguments travel as environment data, never shell source.
        env['PLCFORGE_UPGRADE_CLI'] = str(shim.resolve())
        env['PLCFORGE_UPGRADE_ARGS'] = json.dumps(arguments)
        return execute(['powershell.exe','-NoLogo','-NoProfile','-NonInteractive','-Command',
                        '$items = @($env:PLCFORGE_UPGRADE_ARGS | ConvertFrom-Json); '
                        '& $env:PLCFORGE_UPGRADE_CLI @items; exit $LASTEXITCODE'],label).strip()

    install(args.baseline,'install-0.1.4')
    old_version = cli(['--version'],'version-before')
    assert old_version=='0.1.4',old_version
    settings = home/'settings.json'
    settings.write_text(json.dumps({'base_url':'https://example.invalid/v1','model':'preserved-user-model',
                                    'api_key':'upgrade-test-fixture-not-a-real-key','api_key_env':''}),encoding='utf-8')
    config_path = home/'plcforge.json'
    config = json.loads(config_path.read_text(encoding='utf-8'))
    config['max_turns'],config['total_token_budget'] = 13,123456
    config_path.write_text(json.dumps(config),encoding='utf-8')
    fixtures = {'codesys/template.project':b'compiler-template-fixture',
                'codesys/toolchain.json':b'{"status":"fixture-not-real-compiler-evidence"}',
                'runtime/preserved-runtime.txt':b'existing-runtime-cache'}
    preserved = {'settings.json':settings,'plcforge.json':config_path}
    for relative, content in fixtures.items():
        path = home/relative
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(content)
        preserved[relative] = path
    source = project/'src/PLC_PRG.st'
    source.parent.mkdir()
    source.write_text('PROGRAM PLC_PRG END_PROGRAM',encoding='utf-8')
    preserved['project/src/PLC_PRG.st'] = source
    session = project/'.plcforge/sessions/existing/state.json'
    session.parent.mkdir(parents=True)
    session.write_text('{"status":"preserved-fixture"}',encoding='utf-8')
    preserved['project/session'] = session
    obsolete = prefix/'node_modules/plcforge/old-version-only-fixture.txt'
    obsolete.write_text('old package content',encoding='utf-8')
    before = {name:sha(path) for name,path in preserved.items()}
    install(args.current,'install-0.1.5',args.registry)
    new_version = cli(['--version'],'version-after')
    doctor = json.loads(cli(['doctor'],'doctor-after'))
    after = {name:sha(path) for name,path in preserved.items()}
    assert new_version=='0.1.5' and doctor['plcforge']=='0.1.5'
    assert doctor['model']=='preserved-user-model'
    assert before==after,'User files changed during upgrade'
    assert not obsolete.exists(),'Old package directory was not replaced'
    package = json.loads((prefix/'node_modules/plcforge/package.json').read_text(encoding='utf-8'))
    assert package['version']=='0.1.5'
    archive_files = 0
    with tarfile.open(args.current) as archive:
        for item in archive:
            if not item.isfile():
                continue
            relative = Path(item.name).relative_to('package')
            actual = prefix/'node_modules/plcforge'/relative
            assert actual.read_bytes()==archive.extractfile(item).read(),str(relative)+' differs from tested archive'
            archive_files += 1
    report = {'status':'passed','at':datetime.now(timezone.utc).isoformat(),
              'method':'real npm registry global upgrade' if args.registry else 'real offline npm global install into the same isolated prefix',
              'installed_from_registry':args.registry, 'installed_files_match_tested_archive':archive_files,
              'before_version':old_version,'after_version':new_version,'python_loaded_plcforge_version':doctor['plcforge'],
              'one_active_package':True,'old_package_file_removed':True,
              'preserved_files':list(preserved),'preserved_hashes':after,
              'credentials':'synthetic test fixture; actual user credentials were not read',
              'baseline_archive_sha256':sha(args.baseline),'new_archive_sha256':sha(args.current),
              'automatic_update_scope':'install invocation only; no background self-update or version polling',
              'processes':'already-running terminals must restart to load upgraded code',
              'codesys_scope':'configuration/template preservation only; no compiler run in this upgrade test'}
    report_path = ROOT/'evidence/npm-upgrade-0.1.5.json'
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
    print(json.dumps({'status':report['status'],'before':old_version,'after':new_version,
                      'preserved_files':len(preserved),'report':str(report_path),'logs':str(run)},ensure_ascii=False))


if __name__=='__main__':
    main()
