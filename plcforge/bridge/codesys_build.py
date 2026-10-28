# -*- coding: utf-8 -*-
"""Runs INSIDE the CODESYS Scripting/IronPython engine, NOT normal Python.

Offline build only. No online login, download, write, or PLC start command.
Compatibility target: CODESYS 3.5 Scripting; actual installed profile must be tested.
"""
from __future__ import print_function
import io
import json
import os
import traceback


def build_job(job):
    from scriptengine import projects, system, Severity, KindOfTask
    result = {'job_id': job['job_id'], 'source_hashes': job['source_hashes'],
              'status': 'infrastructure_error', 'build_completed': False,
              'generate_code_completed': False, 'diagnostics': [], 'error_count': None}
    project = None
    try:
        project = projects.open(job['project'])
        apps = [x for x in project.get_children(True)
                if getattr(x, 'is_application', False) and x.get_name() == job['application']]
        if len(apps) != 1:
            raise RuntimeError('Expected one application named ' + job['application'])
        app = apps[0]
        imported = []
        for pou in job['pous']:
            matches = app.find(pou['name'], False)
            if len(matches) > 1:
                raise RuntimeError('Ambiguous POU name ' + pou['name'])
            if matches:
                obj = matches[0]
            elif pou['kind'] == 'FUNCTION':
                obj = app.create_function(pou['name'], pou['return_type'])
            else:
                obj = app.create_program(pou['name']) if pou['kind'] == 'PROGRAM' else app.create_function_block(pou['name'])
            obj.textual_declaration.replace(new_text=pou['declaration'])
            obj.textual_implementation.replace(new_text=pou['implementation'])
            imported.append(pou['name'])
        # Force every supplied POU into a task-reachable call graph.
        declarations, calls = [], []
        for index, pou in enumerate(job['pous']):
            if pou['kind'] == 'FUNCTION_BLOCK':
                instance = 'PLCFORGE_instance_' + str(index)
                declarations.append('    ' + instance + ' : ' + pou['name'] + ';')
                calls.append(instance + '();')
            elif pou['kind'] == 'PROGRAM':
                calls.append(pou['name'] + '();')
            elif pou['kind'] == 'FUNCTION':
                instance = 'PLCFORGE_result_' + str(index)
                declarations.append('    ' + instance + ' : ' + pou['return_type'] + ';')
                calls.append(instance + ' := ' + pou['name'] + '();')
        probes = app.create_program('PLCFORGE_CompileProbe')
        probes.textual_declaration.replace(new_text='PROGRAM PLCFORGE_CompileProbe\nVAR\n' + '\n'.join(declarations) + '\nEND_VAR')
        probes.textual_implementation.replace(new_text='\n'.join(calls))
        tasks = [obj for obj in app.get_children(True)
                 if getattr(obj, 'is_task', False) and obj.kind_of_task == KindOfTask.Cyclic]
        if not tasks:
            raise RuntimeError('Template must contain an application with a cyclic task')
        tasks[0].pous.add('PLCFORGE_CompileProbe')
        result['probe_task'] = tasks[0].get_name()
        for category in system.get_message_categories():
            system.clear_messages(category)
        result['build_started'] = True
        app.rebuild()
        result['build_completed'] = True
        if job.get('generate_code', True):
            app.generate_code()
            result['generate_code_completed'] = True
        result['status'] = 'passed'
        result['imported_pous'] = imported
        project.save()
    except Exception:
        result['status'] = 'infrastructure_error'
        result['diagnostics'].append({'severity': 'infrastructure', 'message': traceback.format_exc()})
    finally:
        error_count = 0
        for category in system.get_message_categories():
            for label,levels in (('error',Severity.Error | Severity.FatalError),('warning',Severity.Warning)):
                for msg in system.get_message_objects(category=category,severities=levels):
                    error_count += 1 if label=='error' else 0
                    result['diagnostics'].append({'severity':label,'message':msg.text,
                        'code':(str(msg.prefix)+str(msg.number)) if msg.number is not None else None,
                        'position':msg.position_text,
                        'object':msg.object.get_name() if msg.object is not None else None})
        result['error_count'] = error_count
        if error_count and result.get('build_started'):
            result['status'] = 'failed'
        if project is not None:
            project.close()
        with io.open(job['result'], 'w', encoding='utf-8') as stream:
            stream.write(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def run():
    from scriptengine import system
    with io.open(os.environ['PLCFORGE_CODESYS_JOB'], 'r', encoding='utf-8') as stream:
        job = json.load(stream)
    result = build_job(job)
    system.exit(0 if result['status'] == 'passed' else 1)


if __name__ == '__main__':
    run()
