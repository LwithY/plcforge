"""Build a NEW P01 acceptance suite; never claim it is the collaborator's oracle."""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from plcforge.evidence import canonical_hash
from plcforge.security import write_json
from plcforge.tasks import development_hash

INPUTS = ('bottleSensor','cleaningConfirmButton','fillingConfirmButton','cappingConfirmButton','packingConfirmButton','finishedButton')
OUTPUTS = ('Pump_Motor','Filling_Valve','Capping_Machine','Packing_Machine','Completion_Light')

SOURCE = '''PROGRAM PLC_PRG
VAR_INPUT
    bottleSensor, cleaningConfirmButton, fillingConfirmButton : BOOL;
    cappingConfirmButton, packingConfirmButton, finishedButton : BOOL;
END_VAR
VAR_OUTPUT
    Pump_Motor, Filling_Valve, Capping_Machine, Packing_Machine, Completion_Light : BOOL;
END_VAR
VAR
    phase : INT := 0;
END_VAR
CASE phase OF
    0: IF bottleSensor THEN phase := 1; END_IF;
    1: IF cleaningConfirmButton THEN phase := 2; END_IF;
    2: IF fillingConfirmButton THEN phase := 3; END_IF;
    3: IF cappingConfirmButton THEN phase := 4; END_IF;
    4: IF packingConfirmButton THEN phase := 5; END_IF;
    5: IF finishedButton THEN phase := 0; END_IF;
END_CASE;
Pump_Motor := phase = 1;
Filling_Valve := phase = 2;
Capping_Machine := phase = 3;
Packing_Machine := phase = 4;
Completion_Light := phase = 5;
END_PROGRAM
'''


def inputs(*active):
    return {name: name in active for name in INPUTS}


def sequence(name, values):
    # Independent transition-table oracle, not ST parsing or executing the fixture.
    phase, scans = 0, []
    for signals in values:
        if signals[INPUTS[phase]]:
            phase = (phase+1) % 6
        scans.append({'inputs':signals, 'expect':{name:phase==index+1 for index,name in enumerate(OUTPUTS)}})
    return {'name':name, 'scans':scans}


def build(original):
    cycle = [inputs(name) for name in INPUTS]
    idle = inputs()
    all_high = inputs(*INPUTS)
    development = [sequence('normal_cycle', [idle]+cycle+[idle]*3),
        sequence('all_levels_one_transition_per_scan', [all_high]*10),
        sequence('out_of_order_buttons_not_queued', [inputs(*INPUTS[2:]),inputs(INPUTS[0])]+[idle]*8),
        sequence('sensor_high_restart_after_completion', [inputs(INPUTS[0],name) for name in INPUTS]+[idle]*4),
        sequence('wait_in_cleaning', [cycle[0]]+[idle]*8+[cycle[1]]),
        sequence('early_confirmation_pulse', [inputs(INPUTS[1]),cycle[0]]+[idle]*8),
        sequence('complete_waits_for_finished', cycle[:5]+[idle]*3),
        sequence('continuous_cycle', cycle+[idle]*2)]
    assert sum(len(c['scans']) for c in development)==76
    conventions = [
        ('C1','Only the active stage output is TRUE; all five outputs are FALSE in idle.'),
        ('C2','Buttons and bottleSensor are sampled as levels, not rising edges.'),
        ('C3','At most one state transition per scan; outputs describe the resulting state in the same scan.'),
        ('C4','After finishedButton resets completion, a still-high bottleSensor starts cleaning on the next scan.'),
        ('C5','An irrelevant early confirmation pulse is ignored and is never queued.'),
        ('C6','Each test starts in idle; omitted inputs retain their previous value; no timer is required.')]
    contract = {'version':'P01-pilot-contract-v1', 'original_requirements':original,
        'conventions':[{'id':key,'text':text,'origin':'handoff 2026-10-28 section 7; reconstructed locally',
                        'review_status':'pending_confirmation'} for key,text in conventions],
        'pending_questions':['Confirm level triggering, output exclusivity, same-scan outputs, restart and no queued confirmations.'],
        'review_status':'pending_confirmation',
        'implementation_basis':'Implement the supplied provisional conventions for this pilot; they remain pending human review.',
        'scan_semantics':'Sample before scan; observe outputs after same scan; persistent state; fresh state per test.'}
    task = {'id':'P01','title':'Automated Filling Line Control','prompt':original,
        'interface':{'pou':'PLC_PRG','kind':'PROGRAM','inputs':dict.fromkeys(INPUTS,'BOOL'), 'outputs':dict.fromkeys(OUTPUTS,'BOOL')},
        'entry_file':'src/PLC_PRG.st','scan_ms':20,'scan_semantics':contract['scan_semantics'],
        'specs':[{'id':'exclusive','formula':'G (!(Pump_Motor & Filling_Valve) & !(Filling_Valve & Capping_Machine) & !(Capping_Machine & Packing_Machine) & !(Packing_Machine & Completion_Light))'}],
        'tests':development,'contract':contract,
        'provenance':{'source':'question bank easy[0]', 'review_status':'pending_confirmation',
            'suite_origin':'new local reconstruction; collaborator original files unavailable', 'suite_version':'P01-development-v1'}}
    task['development_suite_sha256'] = development_hash(task)
    independent = []
    for phase in range(6):
        for bits in itertools.product((False,True), repeat=6):
            independent.append(sequence('phase_%d_inputs_%s' % (phase, ''.join(str(int(b)) for b in bits)),
                                        cycle[:phase]+[dict(zip(INPUTS,bits))]))
    independent += [sequence('long_wait_three_cycles', sum(([idle]*12+[event] for event in cycle*3), [])),
                    sequence('sensor_remains_high', [inputs(INPUTS[0],name) for name in INPUTS]+[inputs(INPUTS[0])]*5),
                    sequence('early_confirmations_not_queued', [inputs(*INPUTS[1:]), cycle[0]]+[idle]*8+cycle[1:])]
    assert len(independent)==387
    oracle = {'task_id':'P01','version':'P01-independent-v1-reconstructed', 'scan_ms':20,
              'contract_sha256':canonical_hash(contract), 'origin':'new local oracle; not the collaborator original',
              'visibility':'host only, outside each model workspace', 'tests':independent}
    write_json(ROOT/'data/tasks/P01.json', task)
    write_json(ROOT/'evaluation/P01/holdout.json', oracle)
    path = ROOT/'evaluation/P01/holdout.json'
    write_json(ROOT/'evaluation/P01/manifest.json', {'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'version':oracle['version'], 'contract_sha256':oracle['contract_sha256'], 'cases':len(independent),
        'scans':sum(len(c['scans']) for c in independent), 'development_cases':8, 'development_scans':76,
        'development_suite_sha256':task['development_suite_sha256']})
    source_path = ROOT/'examples/reference_solutions/P01.st'
    source_path.write_text(SOURCE, encoding='utf-8')
    print(json.dumps({'task':str(ROOT/'data/tasks/P01.json'), 'independent_manifest':str(path.parent/'manifest.json')}))


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--question-bank',type=Path)
    args = parser.parse_args()
    original = (json.loads(args.question_bank.read_text(encoding='utf-8-sig'))['easy'][0]['en'] if args.question_bank else
                json.loads((ROOT/'data/tasks/P01.json').read_text(encoding='utf-8'))['contract']['original_requirements'])
    build(original)
