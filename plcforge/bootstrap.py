"""Initialize a per-user npm installation and connect an installed CODESYS toolchain."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import string
import subprocess

from . import __version__
from .compiler import CodesysCompiler, codesys_command, terminate_process_tree
from .config import Config, application_home, settings_template
from .security import redact, sensitive_values, write_json


def discover_codesys(location=None):
    roots = []
    if location:
        selected = Path(location).expanduser().resolve()
        if selected.is_file() and selected.name.lower()=='codesys.exe':
            roots.append(selected.parent.parent)
        elif selected.is_dir():
            roots.append(selected)
        else:
            raise ValueError('CODESYS location must be an installed CODESYS.exe or installation folder')
    else:
        if os.environ.get('CODESYS_DIR'):
            roots.append(Path(os.environ['CODESYS_DIR']))
        for name in ('ProgramFiles','ProgramFiles(x86)'):
            if os.environ.get(name):
                roots.extend(Path(os.environ[name]).glob('*CODESYS*'))
        if os.name=='nt':
            roots.extend(Path(letter+':/CODESYS') for letter in string.ascii_uppercase
                         if Path(letter+':/CODESYS').is_dir())
    found = []
    for root in dict.fromkeys(roots):
        if not root.is_dir():
            continue
        # Search installed vendor folders only, never recursively scan all disks.
        for executable in root.glob('**/Common/CODESYS.exe'):
            profiles = executable.parent.parent/'Profiles'
            for profile in profiles.glob('CODESYS V*.profile.xml'):
                template = executable.parent.parent/'Templates/Standard.project'
                if template.is_file():
                    label = profile.name[:-len('.profile.xml')]
                    version = tuple(int(x) for x in re.findall(r'\d+',label))
                    found.append((version,{'executable':str(executable.resolve()),'profile':label,
                                            'vendor_template':str(template.resolve())}))
    return max(found,key=lambda x:x[0])[1] if found else None


def trusted_env():
    secrets = sensitive_values()
    return {k:v for k,v in os.environ.items()
            if not re.search(r'(API_KEY|TOKEN|SECRET|PASSWORD)$',k,re.I) and v not in secrets}


def prepare_template(config, source, home):
    directory = home/'codesys'
    directory.mkdir(parents=True,exist_ok=True)
    source_copy = directory/'vendor-source.project'
    shutil.copy2(source,source_copy)
    target = directory/'template.project'
    report_path = directory/'template-report.json'
    script = Path(__file__).parent/'bridge/prepare_template.py'
    argv,command = codesys_command(config,script)
    env = trusted_env()
    env.update(PLCFORGE_TEMPLATE_SOURCE=str(source_copy),PLCFORGE_TEMPLATE_OUTPUT=str(target),
               PLCFORGE_TEMPLATE_REPORT=str(report_path))
    with (directory/'template-setup.log').open('w',encoding='utf-8') as log:
        process = subprocess.Popen(command if os.name=='nt' else argv,cwd=home,env=env,
                                   stdout=log,stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            process.wait(timeout=240)
        except (subprocess.TimeoutExpired,KeyboardInterrupt):
            terminate_process_tree(process)
            raise
    report = json.loads(report_path.read_text(encoding='utf-8-sig')) if report_path.is_file() else {}
    if process.returncode or report.get('status')!='passed' or not target.is_file():
        raise ValueError('CODESYS template preparation failed; see '+str(directory/'template-setup.log'))
    return target


def setup(home=None, codesys=None, skip_codesys=False):
    home = Path(home or application_home()).expanduser().resolve()
    home.mkdir(parents=True,exist_ok=True)
    config_path,settings_path = home/'plcforge.json',home/'settings.json'
    if not config_path.exists():
        write_json(config_path,Config().public())
    if not settings_path.exists():
        write_json(settings_path,settings_template())
    config = Config.load(config_path,apply_env=False)
    proof = CodesysCompiler(config.codesys).doctor()
    report = {'version':__version__,'at':datetime.now(timezone.utc).isoformat(),
              'home':str(home),'settings_file':str(settings_path),'status':'core_ready','codesys':proof}
    if not skip_codesys and os.environ.get('PLCFORGE_SKIP_CODESYS_SETUP')!='1':
        if proof['status']=='ready_verified' and not codesys:
            report['status']='ready_verified'
        else:
            try:
                installed = discover_codesys(codesys or config.codesys.get('executable') or None)
                if installed:
                    print('[PLCForge setup] Found '+installed['profile']+'. Preparing offline compiler...',flush=True)
                    changed = (config.codesys.get('executable')!=installed['executable'] or
                               config.codesys.get('profile')!=installed['profile'])
                    config.codesys.update(executable=installed['executable'],profile=installed['profile'],
                                          application='Application',additionalfolder='')
                    template = config.codesys.get('template_project')
                    if changed or not template or not Path(template).is_file():
                        config.codesys['template_project']=str(prepare_template(config.codesys,installed['vendor_template'],home))
                    config.codesys['verification_receipt']=str(home/'codesys/toolchain.json')
                    print('[PLCForge setup] Verifying the CODESYS build bridge...',flush=True)
                    compiler = CodesysCompiler(config.codesys)
                    build = compiler.compile({'src/PLC_PRG.st':
                        'PROGRAM PLC_PRG\nVAR\nWarmupValue : BOOL;\nEND_VAR\nWarmupValue := FALSE;\nEND_PROGRAM'},
                        home/'codesys/verification-builds')
                    if build.get('status')=='passed':
                        write_json(Path(config.codesys['verification_receipt']),{
                            'status':'passed','compiler_fingerprint':compiler.fingerprint(),
                            'source_hashes':build['source_hashes'],'verification':'real offline bridge compile',
                            'at':datetime.now(timezone.utc).isoformat()})
                        report['status']='ready_verified'
                    else:
                        report.update(status='needs_codesys_setup',compile_result=build)
                    write_json(config_path,config.public())
                else:
                    report['note']='CODESYS not found. Install CODESYS with Scripting, then run plcforge setup.'
            except (OSError,ValueError,subprocess.TimeoutExpired) as exc:
                report.update(status='needs_codesys_setup',error=redact(str(exc)))
    report['codesys']=CodesysCompiler(config.codesys).doctor()
    write_json(home/'installation.json',report)
    print('[PLCForge setup] '+report['status']+' | settings: '+str(settings_path),flush=True)
    if report.get('note') or report.get('error'):
        print('[PLCForge setup] '+report.get('note',report.get('error','')),flush=True)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--home',type=Path)
    parser.add_argument('--codesys',type=Path)
    parser.add_argument('--skip-codesys',action='store_true')
    args = parser.parse_args()
    setup(args.home,args.codesys,args.skip_codesys)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
