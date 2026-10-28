# -*- coding: utf-8 -*-
"""Run independent compilation jobs in one real CODESYS process for validation."""
from __future__ import print_function
import io
import json
import os
import sys

from scriptengine import system

with io.open(os.environ['PLCFORGE_BATCH_MANIFEST'],'r',encoding='utf-8') as stream:
    manifest = json.load(stream)
sys.path.insert(0,manifest['bridge_directory'])
from codesys_build import build_job

results = []
for job in manifest['jobs']:
    print('PLCFORGE compiling ' + job['case_name'])
    result = build_job(job)
    results.append({'case':job['case_name'],'status':result['status'],'job_id':job['job_id']})
    with io.open(manifest['progress'],'w',encoding='utf-8') as stream:
        stream.write(json.dumps(results,ensure_ascii=False,indent=2))
    if result['status'] == 'infrastructure_error':
        break
system.exit(0 if len(results)==len(manifest['jobs']) else 1)
