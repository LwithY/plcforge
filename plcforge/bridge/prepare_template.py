# -*- coding: utf-8 -*-
"""Upgrade a COPY of the vendor's Standard.project to an installed Win target."""
from __future__ import print_function
import io
import json
import os
import traceback

from scriptengine import projects, system, device_repository, librarymanager, Severity, VersionUpdateFlags

report = {'status':'started','devices':[],'diagnostics':[]}
project = None
try:
    devices = device_repository.get_all_devices(name='CODESYS Control Win', source=None)
    candidates = []
    for device in devices:
        ident = device.device_id
        name = device.device_info.name
        item = {'name':name,'type':ident.type,'id':ident.id,'version':ident.version}
        report['devices'].append(item)
        if ident.type == 4096:
            candidates.append((1 if 'x64' in name.lower() or '64' in name.lower() else 0,
                               tuple(int(x) for x in ident.version.split('.')),item))
    if not candidates:
        raise RuntimeError('No installed CODESYS Control Win device description found')
    selected = sorted(candidates,key=lambda x:(x[0],x[1]))[-1][2]
    report['selected_device'] = selected
    project = projects.open(os.environ['PLCFORGE_TEMPLATE_SOURCE'],
                            update_flags=VersionUpdateFlags.SilentMode | VersionUpdateFlags.UpdateAll)
    targets = [obj for obj in project.get_children(True) if getattr(obj,'is_device',False)]
    if len(targets)!=1:
        raise RuntimeError('Expected one top-level target in Standard.project copy')
    targets[0].update(selected['type'],selected['id'],selected['version'])
    targets[0].rename('Device')
    apps = [obj for obj in project.get_children(True) if getattr(obj,'is_application',False)]
    if len(apps)!=1:
        raise RuntimeError('Expected one Application in the standard template')
    app = apps[0]
    library_managers = [obj for obj in app.get_children(True) if getattr(obj,'is_libman',False)]
    report['libraries_before'] = []
    for manager in library_managers:
        names = list(manager.get_libraries())
        report['libraries_before'].extend([str(n) for n in names])
        for name in names:
            if 'IoStandard, 3.1.3.1' in str(name):
                manager.remove_library(name)
    standard = []
    for entry in librarymanager.get_all_libraries(exclude_shadowed_libs=True):
        lib = entry[0] if isinstance(entry,(list,tuple)) else entry
        if lib.title == 'Standard':
            standard.append(lib)
    report['standard_libraries'] = [lib.displayname for lib in standard]
    if not standard:
        raise RuntimeError('Standard library is not installed')
    latest = sorted(standard,key=lambda lib:tuple(int(x) for x in str(lib.version).split('.')))[-1]
    for manager in library_managers:
        if not any('Standard,' in str(n) and 'IoStandard' not in str(n) for n in manager.get_libraries()):
            manager.add_library(latest)
        for reference in manager.references:
            if reference.is_managed and reference.managed_library.title=='Standard':
                reference.qualified_only = False
    pous = app.find('PLC_PRG',False)
    if len(pous)!=1:
        raise RuntimeError('Expected one PLC_PRG')
    pous[0].textual_declaration.replace(new_text='PROGRAM PLC_PRG\nVAR\n    WarmupValue : BOOL;\nEND_VAR')
    pous[0].textual_implementation.replace(new_text='WarmupValue := FALSE;')
    project.save_as(os.environ['PLCFORGE_TEMPLATE_OUTPUT'])
    for category in system.get_message_categories():
        system.clear_messages(category)
    app.rebuild()
    report['build_completed'] = True
    app.generate_code()
    report['generate_code_completed'] = True
    errors = []
    for category in system.get_message_categories():
        errors.extend(m.text for m in system.get_message_objects(category=category,severities=Severity.Error | Severity.FatalError))
    report['diagnostics'] = errors
    report['error_count'] = len(errors)
    report['status'] = 'passed' if not errors else 'failed'
    project.save()
except Exception:
    report['status'] = 'failed'
    report['error'] = traceback.format_exc()
finally:
    if project is not None:
        project.close()
    with io.open(os.environ['PLCFORGE_TEMPLATE_REPORT'],'w',encoding='utf-8') as stream:
        stream.write(json.dumps(report,ensure_ascii=False,indent=2))
system.exit(0 if report['status']=='passed' else 1)
