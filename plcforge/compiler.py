"""Real CODESYS adapter. Absence of CODESYS is an explicit unavailable result."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid

from .security import digest, redact, write_json
from .st import STError, strip_comments


def source_sections(source: str) -> dict:
    clean = strip_comments(source)
    if len(re.findall(r'\b(?:PROGRAM|FUNCTION_BLOCK|FUNCTION)\s+\w+',clean,re.I)) != 1:
        raise STError('Exactly one POU is required per source file')
    match = re.match(r'\s*(PROGRAM|FUNCTION_BLOCK|FUNCTION)\s+(\w+)(?:\s*:\s*(\w+))?', clean, re.I)
    if not match:
        raise STError('CODESYS bridge requires one complete POU per file')
    kind, name = match.group(1).upper(), match.group(2)
    end = re.search(r'\bEND_' + kind + r'\s*;?\s*$', clean, re.I)
    if not end:
        raise STError('Missing POU terminator for ' + name)
    last = match.end()
    for block in re.finditer(r'\bVAR(?:_INPUT|_OUTPUT|_IN_OUT|_TEMP|_GLOBAL|_EXTERNAL)?\b.*?\bEND_VAR\b',
                             clean[match.end():end.start()], re.I|re.S):
        last = match.end()+block.end()
    if re.search(r'\b(PROGRAM|FUNCTION_BLOCK|FUNCTION)\b', clean[last:end.start()], re.I):
        raise STError('Multiple POUs in one file are not supported by the bridge')
    return {'name': name, 'kind': kind, 'return_type': match.group(3) or '',
            'declaration': source[:last].strip(), 'implementation': source[last:end.start()].strip(),
            'sha256': digest(source)}


def terminate_process_tree(process: subprocess.Popen) -> None:
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    else:
        process.kill()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def codesys_command(config: dict, script: Path) -> tuple[list[str], str]:
    """CODESYS parses its raw Windows command line, requiring quoted VALUES.

    subprocess's default list encoding quotes the whole --profile=value token
    when the profile contains spaces; CODESYS 3.5.22.40 rejects that form.
    """
    def quoted(value: str) -> str:
        if any(c in value for c in ('"','\r','\n','\x00')):
            raise ValueError('Invalid character in CODESYS command argument')
        return '"'+value+'"'
    executable = str(Path(config['executable']).resolve())
    profile = config['profile']
    script = str(script.resolve())
    argv = [executable,'--profile='+profile,'--culture=en','--noUI','--runscript='+script]
    command = quoted(executable)+' --profile='+quoted(profile)+' --culture=en --noUI --runscript='+quoted(script)
    if config.get('additionalfolder'):
        folder = str(Path(config['additionalfolder']).resolve())
        argv.append('--additionalfolder='+folder)
        command += ' --additionalfolder='+quoted(folder)
    return argv,command


class CodesysCompiler:
    def __init__(self, config: dict):
        self.config = config

    def fingerprint(self) -> str:
        """Invalidate cached results after configuring or changing the real toolchain."""
        files = {}
        import hashlib
        bridge=Path(__file__).parent/'bridge'/'codesys_build.py'
        files['bridge']=hashlib.sha256(bridge.read_bytes()).hexdigest()
        for key in ('executable','template_project'):
            value = self.config.get(key)
            if value and Path(value).is_file():
                files[key] = hashlib.sha256(Path(value).read_bytes()).hexdigest()
        executable = self.config.get('executable')
        if executable:
            profile = Path(executable).parent.parent/'Profiles'/(self.config.get('profile','')+'.profile.xml')
            if profile.is_file():
                files['profile'] = hashlib.sha256(profile.read_bytes()).hexdigest()
        toolchain_config={k:v for k,v in self.config.items() if k!='verification_receipt'}
        return digest(json.dumps({'config':toolchain_config,'files':files},sort_keys=True))

    def doctor(self) -> dict:
        checks = {key: bool(self.config.get(key)) for key in ('executable', 'profile', 'template_project', 'application')}
        for key in ('executable', 'template_project'):
            checks[key] = bool(self.config.get(key)) and Path(self.config[key]).is_file()
        if self.config.get('additionalfolder'):
            checks['additionalfolder'] = Path(self.config['additionalfolder']).is_dir()
        verified=False
        receipt=self.config.get('verification_receipt')
        if all(checks.values()) and receipt and Path(receipt).is_file():
            try:
                proof=json.loads(Path(receipt).read_text(encoding='utf-8-sig'))
                verified=proof.get('status')=='passed' and proof.get('compiler_fingerprint')==self.fingerprint()
            except (OSError,ValueError):
                pass
        return {'status': ('ready_verified' if verified else 'configured_unverified') if all(checks.values()) else 'unavailable',
                'checks': checks, 'real_compile_verified': verified,
                'next': ('Ready for offline compilation.' if verified else
                         'Run real compilation to verify this configuration.' if all(checks.values()) else
                         'Configure CODESYS executable, exact profile and a valid standard template .project; run compile.')}

    def compile(self, sources: dict[str, str], output: Path, deadline: float | None = None) -> dict:
        started = time.monotonic()
        base = {'engine': 'codesys', 'is_codesys_compile': True,
                'compiler_fingerprint': self.fingerprint(),
                'source_hashes': {p: digest(s) for p,s in sorted(sources.items())}}
        if not sources:
            return dict(base, status='failed', diagnostics=['No ST sources supplied'])
        if self.doctor()['status'] == 'unavailable':
            return dict(base, status='unavailable', diagnostics=[self.doctor()['next']])
        output.mkdir(parents=True, exist_ok=True)
        job_id = uuid.uuid4().hex
        job_dir = output / job_id
        job_dir.mkdir()
        result_path = job_dir / 'result.json'
        try:
            pous = [dict(source_sections(s), path=p) for p,s in sorted(sources.items())]
            if len({p['name'].upper() for p in pous}) != len(pous):
                raise STError('Duplicate POU names')
            if any(p['name'].upper().startswith('PLCFORGE_') for p in pous):
                raise STError('PLCFORGE_ is reserved for bridge-generated objects')
            project_path = job_dir / 'build.project'
            shutil.copy2(self.config['template_project'], project_path)
            job = {'job_id': job_id, 'project': str(project_path.resolve()),
                   'result': str(result_path.resolve()), 'pous': pous,
                   'application': self.config['application'],
                   'source_hashes': base['source_hashes'],
                   'generate_code': self.config.get('generate_code', True)}
            write_json(job_dir / 'job.json', job)
            bridge = Path(__file__).parent / 'bridge' / 'codesys_build.py'
            args,command = codesys_command(self.config,bridge)
            env = {k: v for k,v in os.environ.items()
                   if not re.search(r'(API_KEY|TOKEN|SECRET|PASSWORD)$', k, re.I)}
            env['PLCFORGE_CODESYS_JOB'] = str((job_dir / 'job.json').resolve())
            write_json(job_dir / 'command.json', {'argv': args,'windows_command_line':command})
            remaining = deadline-time.monotonic() if deadline else self.config.get('timeout_seconds', 180)
            if remaining <= 0:
                return dict(base, status='timeout', diagnostics=['Task time budget reached before compilation'])
            timeout = min(self.config.get('timeout_seconds', 180), remaining)
            with (job_dir / 'process.log').open('w', encoding='utf-8') as log:
                process = subprocess.Popen(command if os.name=='nt' else args, cwd=job_dir, env=env, stdout=log, stderr=subprocess.STDOUT,
                                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                try:
                    process.wait(timeout=timeout)
                except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
                    terminate_process_tree(process)
                    if isinstance(exc, KeyboardInterrupt):
                        raise
                    return dict(base, status='timeout', job_id=job_id, artifacts=str(job_dir),
                                elapsed_seconds=time.monotonic()-started)
            if not result_path.is_file():
                return dict(base, status='infrastructure_error', job_id=job_id, artifacts=str(job_dir),
                            diagnostics=['CODESYS did not produce the structured build result',
                                         redact((job_dir / 'process.log').read_text(encoding='utf-8', errors='replace')[-4000:])])
            result = json.loads(result_path.read_text(encoding='utf-8-sig'))
            if result.get('job_id') != job_id or result.get('source_hashes') != base['source_hashes']:
                raise ValueError('Build result identity/hash mismatch')
            status = result.get('status', 'infrastructure_error')
            if status == 'passed' and not (
                process.returncode == 0 and result.get('build_completed') is True and
                result.get('error_count') == 0 and
                (not job['generate_code'] or result.get('generate_code_completed') is True)
            ):
                status = 'infrastructure_error'
            return dict(base, **{k:v for k,v in result.items() if k not in base and k != 'status'},
                        status=status, exit_code=process.returncode, artifacts=str(job_dir),
                        elapsed_seconds=time.monotonic()-started)
        except (OSError, ValueError, STError) as exc:
            return dict(base, status='infrastructure_error', job_id=job_id,
                        artifacts=str(job_dir), diagnostics=[redact(str(exc))])
