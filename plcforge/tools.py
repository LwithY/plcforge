from __future__ import annotations

import json
import difflib
from pathlib import Path
import re

from .compiler import CodesysCompiler
from .ltl import monitor
from .security import Workspace, digest, write_json
from .evidence import canonical_hash, lean, save_report, test_feedback
from .st import ENGINE_VERSION, check_interface, lint, run_tests


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
    tool('test_st', 'Run model-visible development scan tests. Full traces are saved; use read_evidence on demand.',
         {'path':STR, 'tests_path':STR}, ['path']),
    tool('read_evidence', 'Read a bounded page of a development evidence report. Independent evaluation is never exposed.',
         {'report_id': STR, 'start_line': {'type':'integer'}, 'max_lines': {'type':'integer'}}, ['report_id']),
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
        if name == 'read_evidence':
            identifier = args['report_id']
            if not re.fullmatch(r'[0-9a-f]{64}', identifier):
                raise ValueError('Invalid development report ID')
            path = workspace.root/'.plcforge/reports'/(identifier+'.json')
            if path.is_symlink() or not path.resolve().is_relative_to((workspace.root/'.plcforge/reports').resolve()):
                raise ValueError('Invalid evidence location')
            start, count = args.get('start_line', 1), args.get('max_lines', 60)
            if start < 1 or not 1 <= count <= 100:
                raise ValueError('Invalid evidence page')
            lines = path.read_text(encoding='utf-8').splitlines()
            return {'report_id': identifier, 'total_lines': len(lines),
                    'content': '\n'.join('%d: %s' % (i+1, lines[i]) for i in range(start-1, min(len(lines), start-1+count)))}
        if name in ('write_file','edit_file'):
            previous = workspace.path(args['path'])
            previous = previous.read_text(encoding='utf-8-sig') if previous.is_file() else ''
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
            self.state.pop('artifact', None)
            if args['path'].lower().endswith('.st'):
                result['local_syntax'] = lint(content, args['path'])
                try:
                    result['interface'] = check_interface(content, (self.task or {}).get('interface', {}), args['path'])
                except ValueError as exc:
                    result['interface'] = {'status':'failed', 'error':str(exc)}
                history = self.state.setdefault('diagnostic_history', [])
                diagnostics = result['local_syntax'].get('diagnostics', [])+result['interface'].get('diagnostics', [])
                prior = next((h for h in reversed(history) if h['path'] == args['path']), {})
                repeated = [d for d in diagnostics if any(
                    (d.get('code'), d.get('line'), d.get('message')) == (p.get('code'), p.get('line'), p.get('message'))
                    for p in prior.get('diagnostics', []))]
                entry = {'path': args['path'], 'previous_sha256': digest(previous), 'source_sha256': digest(content),
                         'diagnostics': diagnostics, 'repeated': repeated,
                         'diff': '\n'.join(difflib.unified_diff(previous.splitlines(), content.splitlines(), n=2))}
                history.append(entry)
                result['diagnostic_history_id'] = save_report(workspace.root, entry)
                if repeated:
                    result['unresolved_diagnostics'] = repeated
                    result['change_preview'] = entry['diff'][:1200]
                result['save_status'] = 'saved'
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
            result = lint(workspace.path(args['path']).read_text(encoding='utf-8-sig'), args['path'])
            self.state.setdefault('validation', {})['lint'] = result
            return result
        if name == 'test_st':
            task = self.task
            if not task:
                if not args.get('tests_path'):
                    raise ValueError('Without a bound task, provide tests_path JSON containing tests and optional specs')
                task = json.loads(workspace.path(args['tests_path']).read_text(encoding='utf-8-sig'))
            source = workspace.path(args['path']).read_text(encoding='utf-8-sig')
            hashes = {p:digest(s) for p,s in sorted(self.sources().items())}
            suite = {k: task.get(k, [] if k != 'scan_ms' else 20) for k in ('tests', 'specs', 'scan_ms')}
            cache_key = canonical_hash({'sources':hashes, 'entry_source':digest(source), 'path':args['path'],
                                        'suite':suite, 'engine':ENGINE_VERSION})
            cached = self.state.get('test_cache', {}).get(cache_key)
            if cached:
                report_file = workspace.root/cached['report_path']
                try:
                    if canonical_hash(json.loads(report_file.read_text(encoding='utf-8'))) != cached['report_id']:
                        cached = None
                except (OSError, ValueError):
                    cached = None
            if cached:
                self.state.setdefault('validation', {})['tests'] = cached
                return cached
            result = run_tests(source, task.get('tests', []), task.get('scan_ms',20))
            result['test_origin'] = 'bound_task' if self.task else 'workspace_tests'
            result['ltl'] = monitor(task.get('specs', []), result)
            result.update(source_hashes=hashes, suite_sha256=canonical_hash(suite), engine_version=ENGINE_VERSION,
                          scan_config={'scan_ms':task.get('scan_ms',20)}, cache_key=cache_key)
            identifier = save_report(workspace.root, result)
            result.update(report_id=identifier, report_path='.plcforge/reports/'+identifier+'.json')
            feedback = test_feedback(result, task.get('tests', []))
            self.state.setdefault('validation', {})['tests'] = feedback
            self.state.setdefault('test_cache', {})[cache_key] = feedback
            self.state.setdefault('test_history', []).append(dict(feedback, path=args['path'], cache_key=cache_key))
            return feedback
        if name == 'compile_st':
            result = self.compiler.compile(self.sources(), workspace.root / '.plcforge' / 'builds', self.deadline)
            if result.get('status') != 'passed':
                result['diagnostic_category'] = ('native_language' if result.get('status')=='failed' else 'toolchain')
            self.state.setdefault('compile_history', []).append(result)
            self.state.setdefault('validation', {})['compile'] = result
            return result
        if name == 'finish_task':
            return self.final_gate()
        if name == 'request_clarification':
            if not args['question'].strip():
                raise ValueError('A clarification question is required')
            self.state.setdefault('requirement_clarifications', []).append({
                'question':args['question'], 'review_status':'pending_confirmation'})
            return {'status':'needs_input','question':args['question'], 'requirement_review':'pending_confirmation'}
        raise ValueError('Tool not implemented')

    def final_gate(self) -> dict:
        sources = self.sources()
        if not sources:
            return {'status':'needs_repair', 'reason':'Write complete ST POUs under src/ first.'}
        lint_results = {path:lint(source, path) for path,source in sources.items()}
        compile_result = self.state.get('validation',{}).get('compile',{})
        hashes = {p:digest(s) for p,s in sorted(sources.items())}
        tests = None
        target = self.task.get('entry_file', 'src/PLC_PRG.st') if self.task else 'src/PLC_PRG.st'
        try:
            interface = check_interface(sources[target], self.task.get('interface', {}), target) if self.task and target in sources else {
                'status':'not_required' if not self.task else 'failed'}
        except ValueError as exc:
            interface = {'status':'failed','error':str(exc)}
        if not self.task:
            candidates = workspace_tests = [p for p in self.workspace.files('*.json')
                                            if p.relative_to(self.workspace.root).parts[0] == 'tests']
            if candidates and target in sources:
                tests = self.execute('test_st', {'path':target, 'tests_path':str(workspace_tests[0].relative_to(self.workspace.root))})
        elif target in sources:
            tests = self.execute('test_st', {'path':target})
        if (compile_result.get('source_hashes') != hashes or
                compile_result.get('compiler_fingerprint') != self.compiler.fingerprint()):
            # Do not spend native build time on known syntax/interface/test failures.
            if interface['status'] == 'failed' or any(r['status']=='failed' for r in lint_results.values()) or (tests and tests['status']=='failed'):
                compile_result = {'status':'not_run', 'reason':'Repair local failures before native build', 'source_hashes':hashes}
            else:
                compile_result = self.execute('compile_st', {})
        problems = []
        if interface['status'] == 'failed':
            problems.append('Required interface does not match declarations')
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
        elif tests['status'] != 'passed' or tests['ltl']['status']=='unsupported' or any(r['status']!='passed' for r in lint_results.values()):
            status = 'generated_validation_incomplete'
        elif compile_result['status'] == 'unavailable':
            status = 'awaiting_codesys'
        elif compile_result['status'] == 'passed':
            status = 'completed'
        else:
            status = 'generated_validation_incomplete'
        result = {'status':status, 'problems':problems, 'sources':hashes,
                  'interface':interface, 'lint':lint_results, 'tests':tests, 'compile':compile_result,
                  'requirement_review':(self.task or {}).get('contract', {}).get('review_status',
                      (self.task or {}).get('provenance', {}).get('review_status', 'pending_confirmation')),
                  'layers':{'syntax': 'passed' if all(r['status']=='passed' for r in lint_results.values()) else 'incomplete',
                            'subset_scan':(tests or {}).get('status','not_run'),
                            'finite_trace_ltl':(tests or {}).get('ltl',{}).get('status','not_run'),
                            'real_compile':compile_result['status'], 'codesys_runtime':'not_run', 'physical_plc':'not_run'}}
        self.state['validation'] = result
        return result

    def archive(self, gate):
        """Snapshot the exact validated sources without any additional model call."""
        identifier = canonical_hash({'sources':gate.get('sources', {}), 'validation':gate})
        root = self.workspace.root/'.plcforge/artifacts'/identifier
        for path, source in self.sources().items():
            dest = root/path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(source, encoding='utf-8')
        write_json(root/'validation.json', gate)
        self.state['artifact'] = {'status':gate['status'], 'directory':str(root),
                                  'source_hashes':gate.get('sources', {})}
        return self.state['artifact']
