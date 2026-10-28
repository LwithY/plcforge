from __future__ import annotations

import json
from pathlib import Path
import re

from .compiler import CodesysCompiler
from .ltl import monitor
from .security import Workspace, digest
from .st import lint, run_tests


def tool(name, description, properties=None, required=None):
    return {'type': 'function', 'function': {'name': name, 'description': description,
        'parameters': {'type': 'object', 'properties': properties or {}, 'required': required or [],
                       'additionalProperties': False}}}


STR = {'type': 'string'}
SCHEMAS = [
    tool('list_files', 'List allowed workspace text files; default pattern *.', {'pattern': STR}),
    tool('read_file', 'Read a workspace text file with line numbers and its hash.',
         {'path': STR, 'start_line': {'type':'integer'}, 'max_lines': {'type':'integer'}}, ['path']),
    tool('write_file', 'Create or replace a text file. Previous content is checkpointed. One complete POU per .st file under src/.',
         {'path': STR, 'content': STR}, ['path','content']),
    tool('edit_file', 'Replace one exact occurrence. Read file first; expected_sha256 prevents stale edits.',
         {'path':STR, 'old':STR, 'new':STR, 'expected_sha256':STR}, ['path','old','new','expected_sha256']),
    tool('search_code', 'Find literal text in allowed workspace files; bounded results.', {'text':STR}, ['text']),
    tool('plc_knowledge', 'Retrieve compact PLC/CODESYS rules by query: scan, timer, counter, interlock, LTL, types.', {'query':STR}, ['query']),
    tool('plan_tasks', 'Maintain a task plan. Each step has a title and pending/in_progress/completed status.',
         {'steps': {'type':'array', 'items':{'type':'object','properties':{'title':STR,'status':{'type':'string',
             'enum':['pending','in_progress','completed']}},'required':['title','status'],'additionalProperties':False}}}, ['steps']),
    tool('lint_st', 'Parse the supported ST subset. This is not a CODESYS compiler.', {'path':STR}, ['path']),
    tool('test_st', 'Run independent task scan tests if a task is bound, otherwise tests_path JSON (tests list).',
         {'path':STR, 'tests_path':STR}, ['path']),
    tool('compile_st', 'Compile all src/*.st using the configured real CODESYS offline build adapter. Unavailable if not installed.'),
    tool('finish_task', 'Request completion. Rechecks final sources and tests; returns needs_repair on failure. Use after tests and compile attempt.',
         {'summary':STR}, ['summary']),
    tool('request_clarification','Ask the user for missing requirements instead of inventing I/O, priorities or timing. This pauses the request without marking implementation complete.',
         {'question':STR},['question']),
]


def validate_args(value, schema, label='arguments'):
    kind = schema.get('type')
    types = {'object':dict, 'array':list, 'string':str, 'integer':int}
    if kind in types and (not isinstance(value, types[kind]) or kind == 'integer' and type(value) is bool):
        raise ValueError(label + ' must be ' + kind)
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError(label + ' has an invalid value')
    if kind == 'object':
        properties = schema.get('properties', {})
        if any(k not in value for k in schema.get('required', [])):
            raise ValueError(label + ' missing required fields')
        if schema.get('additionalProperties') is False and any(k not in properties for k in value):
            raise ValueError(label + ' contains unexpected fields')
        for key, item in value.items():
            if key in properties:
                validate_args(item, properties[key], label+'.'+key)
    if kind == 'array':
        if len(value) > 100:
            raise ValueError('Array exceeds 100 items')
        for item in value:
            validate_args(item, schema['items'], label+'[]')


class ToolRegistry:
    def __init__(self, workspace: Workspace, compiler: CodesysCompiler, state: dict, task: dict | None = None):
        self.workspace, self.compiler, self.state, self.task = workspace, compiler, state, task
        self.deadline = None
        self.schemas = SCHEMAS

    def sources(self) -> dict[str,str]:
        return {str(p.relative_to(self.workspace.root)).replace('\\','/'):p.read_text(encoding='utf-8-sig')
                for p in self.workspace.files('*.st') if p.relative_to(self.workspace.root).parts[0] == 'src'}

    def execute(self, name: str, args: dict) -> dict:
        schema = next((t['function']['parameters'] for t in self.schemas if t['function']['name'] == name), None)
        if schema is None:
            raise ValueError('Unknown tool: ' + name)
        validate_args(args, schema)
        workspace = self.workspace
        if name == 'list_files':
            pattern = args.get('pattern','*')
            if '..' in pattern or ':' in pattern or pattern.startswith(('/', '\\')):
                raise ValueError('Invalid file pattern')
            return {'files':[str(p.relative_to(workspace.root)).replace('\\','/') for p in workspace.files(pattern)]}
        if name == 'read_file':
            return workspace.read(**{'relative' if k == 'path' else k:v for k,v in args.items()})
        if name in ('write_file','edit_file'):
            if name == 'edit_file':
                old = workspace.path(args['path']).read_text(encoding='utf-8-sig')
                if digest(old) != args['expected_sha256']:
                    raise ValueError('File changed since read; read again before editing')
                if not args['old'] or old.count(args['old']) != 1:
                    raise ValueError('old must match exactly one nonempty occurrence')
                content = old.replace(args['old'], args['new'], 1)
            else:
                content = args['content']
            result = workspace.write(args['path'], content)
            self.state.setdefault('files', {})[args['path']] = result['sha256']
            # Every write invalidates final validation, including changed tests.
            self.state['validation'] = {}
            if args['path'].lower().endswith('.st'):
                result['local_syntax'] = lint(content)
            return result
        if name == 'search_code':
            hits = []
            for path in workspace.files():
                if path.stat().st_size > 512000:
                    continue
                for i,line in enumerate(path.read_text(encoding='utf-8-sig').splitlines()):
                    if args['text'] in line:
                        hits.append({'path':str(path.relative_to(workspace.root)), 'line':i+1, 'text':line[:300]})
                        if len(hits) >= 50:
                            return {'matches':hits, 'truncated':True}
            return {'matches':hits}
        if name == 'plc_knowledge':
            query = args['query'].lower()
            synonyms = {'定时':'timer','计时':'timer','计数':'counter','互锁':'interlock',
                        '急停':'interlock','扫描':'scan','类型':'types','规格':'ltl'}
            for key,val in synonyms.items():
                if key in query:
                    query += ' '+val
            tokens = set(re.findall(r'\w+', query))
            ranked = []
            for path in (Path(__file__).parent / 'knowledge').glob('*.md'):
                text = path.read_text(encoding='utf-8')
                score = sum(token in (path.stem+' '+text).lower() for token in tokens)
                ranked.append((score,path.name,text))
            return {'references':[{'name':name,'text':text} for score,name,text in sorted(ranked,reverse=True)[:3] if score > 0]}
        if name == 'plan_tasks':
            self.state['plan'] = args['steps']
            return {'plan':args['steps']}
        if name == 'lint_st':
            result = lint(workspace.path(args['path']).read_text(encoding='utf-8-sig'))
            self.state.setdefault('validation', {})['lint'] = result
            return result
        if name == 'test_st':
            task = self.task
            if not task:
                if not args.get('tests_path'):
                    raise ValueError('Without a bound task, provide tests_path JSON containing tests and optional specs')
                task = json.loads(workspace.path(args['tests_path']).read_text(encoding='utf-8-sig'))
            result = run_tests(workspace.path(args['path']).read_text(encoding='utf-8-sig'), task.get('tests', []), task.get('scan_ms',20))
            result['test_origin'] = 'bound_task' if self.task else 'workspace_tests'
            result['ltl'] = monitor(task.get('specs', []), result)
            self.state.setdefault('validation', {})['tests'] = result
            self.state.setdefault('test_history', []).append({'path': args['path'], 'status':result['status'],
                'passed':result['passed'], 'total':result['total']})
            return result
        if name == 'compile_st':
            result = self.compiler.compile(self.sources(), workspace.root / '.plcforge' / 'builds', self.deadline)
            self.state.setdefault('compile_history', []).append(result)
            self.state.setdefault('validation', {})['compile'] = result
            return result
        if name == 'finish_task':
            return self.final_gate()
        if name == 'request_clarification':
            if not args['question'].strip():
                raise ValueError('A clarification question is required')
            return {'status':'needs_input','question':args['question']}
        raise ValueError('Tool not implemented')

    def final_gate(self) -> dict:
        sources = self.sources()
        if not sources:
            return {'status':'needs_repair', 'reason':'Write complete ST POUs under src/ first.'}
        lint_results = {path:lint(source) for path,source in sources.items()}
        compile_result = self.state.get('validation',{}).get('compile',{})
        hashes = {p:digest(s) for p,s in sorted(sources.items())}
        if (compile_result.get('source_hashes') != hashes or
                compile_result.get('compiler_fingerprint') != self.compiler.fingerprint()):
            compile_result = self.execute('compile_st', {})
        tests = None
        target = self.task.get('entry_file', 'src/PLC_PRG.st') if self.task else 'src/PLC_PRG.st'
        if not self.task:
            candidates = workspace_tests = [p for p in self.workspace.files('*.json')
                                            if p.relative_to(self.workspace.root).parts[0] == 'tests']
            if candidates and target in sources:
                tests = self.execute('test_st', {'path':target, 'tests_path':str(workspace_tests[0].relative_to(self.workspace.root))})
        elif target in sources:
            tests = self.execute('test_st', {'path':target})
        problems = []
        if any(r['status'] == 'failed' for r in lint_results.values()):
            problems.append('Local syntax/declaration failure')
        if compile_result['status'] in ('failed','timeout','infrastructure_error'):
            problems.append('CODESYS build did not pass')
        if self.task and target not in sources:
            problems.append('Required entry source missing: ' + target)
        if tests is None:
            problems.append('No executable scan tests. Create tests/*.json with independent expected outputs.')
        elif tests['status'] == 'failed' or tests['ltl']['status'] == 'violated':
            problems.append('Functional tests or LTL trace checks failed')
        if problems:
            status = 'needs_repair'
        elif tests['status'] == 'unsupported' or any(r['status']=='unsupported' for r in lint_results.values()):
            status = 'generated_validation_incomplete'
        elif compile_result['status'] == 'unavailable':
            status = 'awaiting_codesys'
        else:
            status = 'completed'
        result = {'status':status, 'problems':problems, 'sources':hashes,
                  'lint':lint_results, 'tests':tests, 'compile':compile_result}
        self.state['validation'] = result
        return result
