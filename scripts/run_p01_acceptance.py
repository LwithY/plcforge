"""Run a new frozen-input model experiment; keep every attempt, including stops."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--baseline-root', type=Path)
parser.add_argument('--model')
args = parser.parse_args()
sys.path.insert(0,str(args.baseline_root or ROOT))
from plcforge import __version__
from plcforge.agent import Agent
from plcforge.config import Config, load_env
from plcforge.progress import TerminalReporter
from plcforge.security import digest, write_json

load_env(ROOT/'.env')
config = Config.load(ROOT/'plcforge.json')
if args.model:
    config.model = args.model
task_path = ROOT/'data/tasks/P01.json'
task = json.loads(task_path.read_text(encoding='utf-8'))
# Freeze exactly the same model-visible payload for both implementations.
payload = {k:task[k] for k in ('id','prompt','interface','specs','tests','contract','scan_semantics','development_suite_sha256')}
payload['model_visibility'] = 'development tests and provisional contract only; independent checks withheld'
prompt = json.dumps(payload,ensure_ascii=False,indent=2)
run_id = datetime.now().strftime('%Y%m%d-%H%M%S-')+('baseline' if args.baseline_root else 'improved')+'-'+uuid.uuid4().hex[:6]
run = ROOT/'.plcforge/acceptance'/run_id
run.mkdir(parents=True,exist_ok=False)
write_json(run/'inputs/task.json',task)
(run/'inputs/prompt.txt').write_text(prompt,encoding='utf-8')
manifest = {'run_id':run_id, 'started_at':datetime.now(timezone.utc).isoformat(),
    'implementation':'baseline-0.1.4' if args.baseline_root else 'improved', 'version':__version__,
    'model':config.model,'base_url':config.base_url,'prompt_sha256':digest(prompt),
    'task_file_sha256':hashlib.sha256(task_path.read_bytes()).hexdigest(),
    'configuration':config.public(), 'independent_suite':'reconstructed locally; original collaborator files unavailable',
    'implementation_sources':{p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in ((args.baseline_root or ROOT)/'plcforge').glob('*.py')},
    'status':'running'}
write_json(run/'manifest.json',manifest)
reporter = TerminalReporter()
print('RUN_DIRECTORY='+str(run),flush=True)
agent = Agent(config,run/'workspace',task,emit=reporter)
try:
    result = agent.run(prompt,mode='code')
finally:
    reporter.close()
write_json(run/'agent_result.json',result)
manifest.update(status=result['status'],session_id=result['session_id'],finished_at=datetime.now(timezone.utc).isoformat())
write_json(run/'manifest.json',manifest)
print(json.dumps({'run_id':run_id,'status':result['status'],'usage':result['usage'],
                  'model_calls':result['model_calls'],'directory':str(run)},ensure_ascii=False),flush=True)
