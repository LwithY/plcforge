"""Fail-closed interpreter for a documented, bounded IEC 61131-3 ST subset.

This is a development test runner, never a substitute for CODESYS compilation,
PLC hardware execution, or unbounded formal model checking.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re


ENGINE_VERSION = 'st-subset-2'


class STError(ValueError):
    def __init__(self, message, *, offset=None, expected=None, actual=None, code='ST_SYNTAX'):
        super().__init__(message)
        self.offset, self.expected, self.actual, self.code = offset, expected, actual, code


class Unsupported(STError):
    pass


def strip_comments(text: str) -> str:
    out, i, depth = [], 0, 0
    while i < len(text):
        if not depth and text[i] in ("'", '"'):
            quote = text[i]
            out.append(quote)
            i += 1
            while i < len(text):
                ch = text[i]
                out.append(ch)
                i += 1
                if ch == '$' and i < len(text):
                    out.append(text[i])
                    i += 1
                elif ch == quote:
                    if i < len(text) and text[i] == quote:
                        out.append(text[i])
                        i += 1
                    else:
                        break
        elif text[i:i+2] == '(*':
            depth += 1
            out.extend('  ')
            i += 2
        elif depth and text[i:i+2] == '*)':
            depth -= 1
            out.extend('  ')
            i += 2
        elif depth:
            out.append('\n' if text[i] == '\n' else ' ')
            i += 1
        elif text[i:i+2] == '//':
            end = text.find('\n', i)
            end = len(text) if end < 0 else end
            out.extend(' ' * (end-i))
            i = end
        else:
            out.append(text[i])
            i += 1
    if depth:
        raise STError('Unterminated (* comment *)')
    return ''.join(out)


TOKEN = re.compile(r'\s+|(?:TIME|T)\#(?:\d+(?:\.\d+)?(?:MS|D|H|M|S))+|'
                   r'\d+\.\d+(?:E[+-]?\d+)?|\d+|[A-Z_][A-Z_0-9]*|'
                   r':=|=>|<=|>=|<>|\*\*|[;:,.()+*/=<>\-]', re.I)


class Tokens(list):
    def __init__(self, source, end):
        super().__init__()
        self.source, self.end, self.positions = source, end, []


def lex(text: str, *, source=None, offset=0) -> list[str]:
    result, pos = Tokens(source or text, offset+len(text)), 0
    while pos < len(text):
        match = TOKEN.match(text, pos)
        if not match:
            raise Unsupported('Unsupported ST token at: ' + text[pos:pos+45], offset=offset+pos,
                              actual=text[pos:pos+1], code='ST_UNSUPPORTED')
        token = match.group()
        if not token.isspace():
            result.append(token.upper())
            result.positions.append(offset+pos)
        pos = match.end()
    return result


def time_ms(token: str) -> float:
    body = token.split('#', 1)[1]
    return sum(float(n) * {'D': 86400000, 'H': 3600000, 'M': 60000, 'S': 1000, 'MS': 1}[u]
               for n, u in re.findall(r'(\d+(?:\.\d+)?)(MS|D|H|M|S)', body))


PRECEDENCE = {'OR': 1, 'XOR': 2, 'AND': 3, '=': 4, '<>': 4, '<': 5, '>': 5,
              '<=': 5, '>=': 5, '+': 6, '-': 6, '*': 7, '/': 7, 'MOD': 7, '**': 8}


class Parser:
    def __init__(self, tokens: list[str]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> str:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else '<EOF>'

    def pop(self, expected: str | None = None) -> str:
        value = self.peek()
        if expected and value != expected:
            index = self.pos-1 if expected == ';' and self.pos and self.tokens[self.pos-1] in ('END_IF', 'END_CASE') else self.pos
            positions = getattr(self.tokens, 'positions', [])
            offset = positions[index] if index < len(positions) else getattr(self.tokens, 'end', None)
            raise STError('Expected %s, got %s at token %d' % (expected, value, self.pos),
                          offset=offset, expected=expected, actual=value, code='ST_EXPECTED_TOKEN')
        if value == '<EOF>':
            raise STError('Unexpected end of input')
        self.pos += 1
        return value

    def expression(self, minimum: int = 0):
        token = self.pop()
        if token in ('NOT', '-', '+'):
            left = ('unary', token, self.expression(9))
        elif token == '(':
            left = self.expression()
            self.pop(')')
        elif token in ('TRUE', 'FALSE'):
            left = ('literal', token == 'TRUE')
        elif '#' in token:
            left = ('literal', time_ms(token))
        elif re.fullmatch(r'\d+(?:\.\d+(?:E[+-]?\d+)?)?', token):
            left = ('literal', float(token) if '.' in token else int(token))
        elif re.fullmatch(r'[A-Z_]\w*', token):
            name = token
            if self.peek() == '.':
                self.pop('.')
                name += '.' + self.pop()
            if self.peek() == '(':
                self.pop('(')
                args = []
                if self.peek() != ')':
                    while True:
                        args.append(self.expression())
                        if self.peek() != ',':
                            break
                        self.pop(',')
                self.pop(')')
                left = ('function', name, args)
            else:
                left = ('variable', name)
        else:
            raise STError('Invalid expression: ' + token)
        while self.peek() in PRECEDENCE and PRECEDENCE[self.peek()] >= minimum:
            op = self.pop()
            right = self.expression(PRECEDENCE[op] + (0 if op == '**' else 1))
            left = ('binary', op, left, right)
        return left

    def block(self, ends: set[str]):
        result = []
        while self.peek() not in ends and self.peek() != '<EOF>':
            if self.peek() == ';':
                self.pop(';')
                continue
            result.append(self.statement())
        return result

    def statement(self):
        token = self.pop()
        if token == 'IF':
            branches = []
            cond = self.expression()
            self.pop('THEN')
            branches.append((cond, self.block({'ELSIF', 'ELSE', 'END_IF'})))
            while self.peek() == 'ELSIF':
                self.pop()
                cond = self.expression()
                self.pop('THEN')
                branches.append((cond, self.block({'ELSIF', 'ELSE', 'END_IF'})))
            other = []
            if self.peek() == 'ELSE':
                self.pop()
                other = self.block({'END_IF'})
            self.pop('END_IF')
            self.pop(';')
            return ('if', branches, other)
        if token == 'CASE':
            expr = self.expression()
            self.pop('OF')
            branches, other = [], []
            while self.peek() not in ('ELSE', 'END_CASE', '<EOF>'):
                labels = [self.expression()]
                while self.peek() == ',':
                    self.pop(',')
                    labels.append(self.expression())
                self.pop(':')
                body = []
                while self.peek() not in ('ELSE', 'END_CASE', '<EOF>'):
                    # CASE labels are literals, optionally negative, or comma-separated literals.
                    tail = self.tokens[self.pos:]
                    if re.match(r'^(?:-?\d+(?: , -?\d+)*) :', ' '.join(tail)):
                        break
                    body.append(self.statement())
                branches.append((labels, body))
            if self.peek() == 'ELSE':
                self.pop()
                other = self.block({'END_CASE'})
            self.pop('END_CASE')
            self.pop(';')
            return ('case', expr, branches, other)
        if token == 'FOR':
            name = self.pop()
            self.pop(':=')
            start = self.expression()
            self.pop('TO')
            end = self.expression()
            step = ('literal', 1)
            if self.peek() == 'BY':
                self.pop()
                step = self.expression()
            self.pop('DO')
            body = self.block({'END_FOR'})
            self.pop('END_FOR')
            self.pop(';')
            return ('for', name, start, end, step, body)
        if token in ('WHILE', 'REPEAT', 'RETURN', 'EXIT'):
            raise Unsupported('Local runner does not support ' + token)
        if self.peek() == ':=':
            self.pop(':=')
            expr = self.expression()
            self.pop(';')
            return ('assign', token, expr)
        if self.peek() == '(':
            self.pop('(')
            args = {}
            while self.peek() != ')':
                name = self.pop()
                self.pop(':=')
                args[name] = self.expression()
                if self.peek() != ',':
                    break
                self.pop(',')
            self.pop(')')
            self.pop(';')
            return ('call', token, args)
        raise Unsupported('Unsupported statement beginning: ' + token)


SCALAR_TYPES = {'BOOL', 'SINT', 'INT', 'DINT', 'LINT', 'USINT', 'UINT', 'UDINT',
                'ULINT', 'REAL', 'LREAL', 'TIME'}
BLOCK_TYPES = {'TON', 'TOF', 'TP', 'R_TRIG', 'F_TRIG', 'CTU', 'CTD'}


@dataclass
class Declaration:
    name: str
    type: str
    scope: str
    initial: object


class Program:
    def __init__(self, source: str):
        text = strip_comments(source)
        header = re.match(r'\s*(PROGRAM|FUNCTION_BLOCK)\s+(\w+)\b', text, re.I)
        if not header:
            raise Unsupported('Use one PROGRAM or FUNCTION_BLOCK per .st file')
        self.kind, self.name = header.group(1).upper(), header.group(2)
        base = header.end()
        text = text[header.end():]
        ending = re.search(r'\bEND_' + self.kind + r'\s*;?\s*$', text, re.I)
        if not ending:
            raise STError('Missing END_' + self.kind)
        body = text[:ending.start()]
        self.declarations = {}
        spans = list(re.finditer(r'\b(VAR_INPUT|VAR_OUTPUT|VAR_IN_OUT|VAR)\b(.*?)\bEND_VAR\b', body, re.I|re.S))
        for match in spans:
            tokens = Parser(lex(match.group(2), source=source, offset=base+match.start(2)))
            while tokens.peek() != '<EOF>':
                names = [tokens.pop()]
                while tokens.peek() == ',':
                    tokens.pop(',')
                    names.append(tokens.pop())
                tokens.pop(':')
                dtype = tokens.pop()
                if dtype not in SCALAR_TYPES | BLOCK_TYPES:
                    raise Unsupported('Unsupported local variable type: ' + dtype,
                                      offset=tokens.tokens.positions[tokens.pos-1], code='ST_UNSUPPORTED')
                initial = ('literal', False if dtype == 'BOOL' else 0)
                if tokens.peek() == ':=':
                    tokens.pop(':=')
                    initial = tokens.expression()
                tokens.pop(';')
                for name in names:
                    if not re.fullmatch(r'[A-Z_]\w*', name) or name in self.declarations:
                        raise STError('Invalid or duplicate variable: ' + name)
                    self.declarations[name] = Declaration(name, dtype, match.group(1).upper(), initial)
        for match in reversed(spans):
            blank = ''.join('\n' if c == '\n' else ' ' for c in body[match.start():match.end()])
            body = body[:match.start()] + blank + body[match.end():]
        parser = Parser(lex(body, source=source, offset=base))
        self.code = parser.block(set())
        self.env = {}
        for name, decl in self.declarations.items():
            if decl.type in BLOCK_TYPES:
                self.env[name] = {'TYPE': decl.type, 'Q': False, 'ET': 0, 'CV': 0,
                                  '_PREV': False, '_ACTIVE': False}
            else:
                self.assign(name, self.evaluate(decl.initial))
        self.operations = 0

    def value(self, name):
        if '.' in name:
            base, field = name.split('.', 1)
            value = self.value(base)
            if not isinstance(value, dict) or field not in {'Q', 'ET', 'CV'}:
                raise STError('Unknown function block output: ' + name)
            return value[field]
        if name not in self.env:
            raise STError('Undeclared variable: ' + name)
        return self.env[name]

    def evaluate(self, node):
        kind = node[0]
        if kind == 'literal':
            return node[1]
        if kind == 'variable':
            return self.value(node[1])
        if kind == 'unary':
            val = self.evaluate(node[2])
            if node[1] == 'NOT':
                if not isinstance(val, bool):
                    raise Unsupported('Local NOT supports BOOL only')
                return not val
            return -val if node[1] == '-' else +val
        if kind == 'function':
            name, args = node[1], [self.evaluate(v) for v in node[2]]
            functions = {'ABS': abs, 'MIN': min, 'MAX': max,
                         'LIMIT': lambda lo, x, hi: min(max(x, lo), hi),
                         'SEL': lambda g, a, b: b if g else a,
                         'INT_TO_REAL': float, 'DINT_TO_REAL': float,
                         'REAL_TO_INT': lambda x: int(round(x)),
                         'REAL_TO_DINT': lambda x: int(round(x)),
                         'INT_TO_DINT': int, 'BOOL_TO_INT': int}
            if name not in functions:
                raise Unsupported('Unsupported local function: ' + name)
            return functions[name](*args)
        a, b, op = self.evaluate(node[2]), self.evaluate(node[3]), node[1]
        if op in {'AND', 'OR', 'XOR'}:
            if type(a) is not bool or type(b) is not bool:
                raise Unsupported('Local boolean operators require BOOL operands')
            return {'AND': a and b, 'OR': a or b, 'XOR': a != b}[op]
        if op == '+': return a+b
        if op == '-': return a-b
        if op == '*': return a*b
        if op == '/':
            return math.trunc(a/b) if type(a) is int and type(b) is int else a/b
        if op == 'MOD': return a-math.trunc(a/b)*b
        if op == '**':
            if abs(b) > 32:
                raise Unsupported('Exponent exceeds local execution bound')
            return a**b
        if op == '=': return a == b
        if op == '<>': return a != b
        if op == '<': return a < b
        if op == '>': return a > b
        if op == '<=': return a <= b
        if op == '>=': return a >= b
        raise Unsupported('Unsupported operator: ' + op)

    def assign(self, name, value):
        if name not in self.declarations:
            raise STError('Assignment to undeclared variable: ' + name)
        dtype = self.declarations[name].type
        if dtype == 'BOOL' and type(value) is not bool:
            raise STError('BOOL assignment requires BOOL: ' + name)
        if dtype in SCALAR_TYPES - {'BOOL'}:
            if type(value) not in (int, float):
                raise STError('Numeric assignment requires a number: ' + name)
            if dtype not in {'REAL', 'LREAL', 'TIME'} and type(value) is not int:
                raise STError('Integer assignment requires explicit conversion: ' + name)
            limits = {'SINT': (-128,127), 'INT': (-32768,32767), 'DINT': (-2147483648,2147483647),
                      'LINT': (-(2**63),2**63-1), 'USINT': (0,255), 'UINT': (0,65535),
                      'UDINT': (0,4294967295), 'ULINT': (0,2**64-1), 'TIME':(0,4294967295)}
            if dtype in limits and not limits[dtype][0] <= value <= limits[dtype][1]:
                raise Unsupported('Integer overflow has target-specific behavior: ' + name)
            if type(value) is float and not math.isfinite(value):
                raise Unsupported('Nonfinite real has target-specific behavior: ' + name)
        self.env[name] = value

    def execute(self, nodes, dt_ms):
        for node in nodes:
            self.operations += 1
            if self.operations > 10000:
                raise STError('Scan operation budget exceeded')
            kind = node[0]
            if kind == 'assign':
                self.assign(node[1], self.evaluate(node[2]))
            elif kind == 'if':
                for cond, body in node[1]:
                    condition = self.evaluate(cond)
                    if type(condition) is not bool:
                        raise STError('IF condition must be BOOL')
                    if condition:
                        self.execute(body, dt_ms)
                        break
                else:
                    self.execute(node[2], dt_ms)
            elif kind == 'case':
                value = self.evaluate(node[1])
                for labels, body in node[2]:
                    if value in [self.evaluate(label) for label in labels]:
                        self.execute(body, dt_ms)
                        break
                else:
                    self.execute(node[3], dt_ms)
            elif kind == 'for':
                start, end, step = [self.evaluate(n) for n in node[2:5]]
                if not all(type(x) is int for x in (start, end, step)) or step == 0:
                    raise STError('FOR requires integers and a nonzero step')
                values = range(start, end+(1 if step > 0 else -1), step)
                if len(values) > 1000:
                    raise STError('FOR iteration budget exceeded')
                for value in values:
                    self.assign(node[1], value)
                    self.execute(node[5], dt_ms)
                self.assign(node[1], start if not values else values[-1]+step)
            elif kind == 'call':
                self.call(node[1], {k: self.evaluate(v) for k,v in node[2].items()}, dt_ms)

    def call(self, name, args, dt):
        fb = self.value(name)
        if not isinstance(fb, dict):
            raise Unsupported('Call target is not a supported function block: ' + name)
        kind = fb['TYPE']
        allowed = {'TON': {'IN','PT'}, 'TOF': {'IN','PT'}, 'TP': {'IN','PT'},
                   'R_TRIG': {'CLK'}, 'F_TRIG': {'CLK'}, 'CTU': {'CU','RESET','PV'},
                   'CTD': {'CD','LOAD','PV'}}[kind]
        if set(args) != allowed:
            raise Unsupported('%s requires named inputs %s in local runner' % (kind, sorted(allowed)))
        if kind in ('R_TRIG', 'F_TRIG'):
            clock = args['CLK']
            fb['Q'] = (clock and not fb['_PREV']) if kind == 'R_TRIG' else (not clock and fb['_PREV'])
            fb['_PREV'] = clock
        elif kind in ('CTU', 'CTD'):
            clock = args['CU' if kind == 'CTU' else 'CD']
            if args['RESET' if kind == 'CTU' else 'LOAD']:
                fb['CV'] = 0 if kind == 'CTU' else args['PV']
            elif clock and not fb['_PREV']:
                fb['CV'] = min(32767, fb['CV']+1) if kind == 'CTU' else max(0, fb['CV']-1)
            fb['Q'] = fb['CV'] >= args['PV'] if kind == 'CTU' else fb['CV'] <= 0
            fb['_PREV'] = clock
        else:
            signal, pt = args['IN'], max(0, args['PT'])
            if kind == 'TON':
                fb['ET'] = min(pt, fb['ET']+dt) if signal and fb['_PREV'] else 0
                fb['Q'] = bool(signal and fb['ET'] >= pt)
            elif kind == 'TOF':
                if signal:
                    fb['Q'], fb['ET'], fb['_ACTIVE'] = True, 0, True
                elif fb['_ACTIVE']:
                    fb['ET'] = 0 if fb['_PREV'] else min(pt, fb['ET']+dt)
                    fb['Q'] = fb['ET'] < pt
                    if not fb['Q']:
                        fb['_ACTIVE'] = False
            elif kind == 'TP':
                if signal and not fb['_PREV'] and not fb['_ACTIVE']:
                    fb['_ACTIVE'], fb['ET'] = True, 0
                elif fb['_ACTIVE']:
                    fb['ET'] = min(pt, fb['ET']+dt)
                fb['Q'] = fb['_ACTIVE'] and fb['ET'] < pt
                if not fb['Q']:
                    fb['_ACTIVE'] = False
            fb['_PREV'] = signal

    def scan(self, inputs: dict, dt_ms: int = 20) -> dict:
        if not isinstance(dt_ms, (int,float)) or dt_ms <= 0:
            raise STError('Scan duration must be positive')
        for name, value in inputs.items():
            key = name.upper()
            if key not in self.declarations or self.declarations[key].scope not in ('VAR_INPUT','VAR_IN_OUT'):
                raise STError('Unknown input: ' + name)
            self.assign(key, value)
        self.operations = 0
        self.execute(self.code, dt_ms)
        return {name: value for name, value in self.env.items() if not isinstance(value, dict)}


def diagnostic(exc, source, path='src/PLC_PRG.st'):
    offset = getattr(exc, 'offset', None)
    located = offset is not None
    offset = min(max(offset or 0, 0), len(source))
    line = source.count('\n', 0, offset)+1
    start = source.rfind('\n', 0, offset)+1
    lines = source.splitlines()
    return {'file': path, 'line': line, 'column': offset-start+1,
            'code': getattr(exc, 'code', 'ST_EXECUTION'), 'message': str(exc),
            'source': lines[line-1] if line <= len(lines) else '', 'location_exact': located,
            'expected': getattr(exc, 'expected', None), 'actual': getattr(exc, 'actual', None),
            'category': 'subset_limitation' if isinstance(exc, Unsupported) else 'language_error'}


def check_interface(source: str, interface: dict, path='src/PLC_PRG.st') -> dict:
    declarations = {}
    clean = strip_comments(source)
    for block in re.finditer(r'\b(VAR_INPUT|VAR_OUTPUT|VAR_IN_OUT|VAR)\b(.*?)\bEND_VAR\b', clean, re.I|re.S):
        for match in re.finditer(r'\b([A-Z_]\w*(?:\s*,\s*[A-Z_]\w*)*)\s*:\s*([A-Z_]\w*)', block.group(2), re.I):
            for name in re.split(r'\s*,\s*', match.group(1)):
                declarations[name.upper()] = (match.group(2).upper(), block.group(1).upper(),
                                             block.start(2)+match.start(1))
    errors = []
    for group, direction in (('inputs', 'VAR_INPUT'), ('outputs', 'VAR_OUTPUT'), ('in_out', 'VAR_IN_OUT')):
        for name, dtype in interface.get(group, {}).items():
            found = declarations.get(name.upper())
            expected_type = dtype.get('type') if isinstance(dtype, dict) else dtype
            if found is None or found[:2] != (expected_type.upper(), direction):
                exc = STError('Interface %s requires %s %s; found %s' %
                              (name, direction, expected_type, found[:2] if found else 'missing'),
                              offset=found[2] if found else 0, code='ST_INTERFACE',
                              expected=direction+' '+expected_type,
                              actual=' '.join(found[:2]) if found else '<missing>')
                item = diagnostic(exc, source, path)
                item.update(category='test_interface', name=name)
                errors.append(item)
    header = re.match(r'\s*(PROGRAM|FUNCTION_BLOCK)\s+(\w+)', clean, re.I)
    if header and ((interface.get('pou') and header.group(2).upper() != interface['pou'].upper()) or
                   (interface.get('kind') and header.group(1).upper() != interface['kind'].upper())):
        errors.append(diagnostic(STError('POU name/kind does not match contract', offset=header.start(),
                                         code='ST_INTERFACE'), source, path))
    return {'status': 'failed' if errors else 'passed', 'diagnostics': errors,
            'scope': 'declared interface names, types, directions and POU'}


def lint(source: str, path='src/PLC_PRG.st') -> dict:
    try:
        program = Program(source)
        # This catches syntax and initialization only; unreachable branches are exercised by tests/CODESYS.
        return {'status': 'passed', 'engine': 'st-subset-parser', 'is_codesys_compile': False,
                'pou': program.name, 'variables': len(program.declarations),
                'scope': 'subset syntax and declaration initialization; no complete type checking'}
    except Unsupported as exc:
        return {'status': 'unsupported', 'engine': 'st-subset-parser', 'is_codesys_compile': False,
                'diagnostics': [diagnostic(exc, source, path)], 'scope': 'supported ST subset only'}
    except (STError, ArithmeticError, TypeError, IndexError, RecursionError) as exc:
        return {'status': 'failed', 'engine': 'st-subset-parser', 'is_codesys_compile': False,
                'diagnostics': [diagnostic(exc, source, path)]}


def run_tests(source: str, cases: list[dict], scan_ms: int = 20) -> dict:
    results = []
    for case in cases:
        trace, failures = [], []
        try:
            program = Program(source)
            scans = case.get('scans', [])
            if not scans or not any(s.get('expect') for s in scans):
                raise STError('Each test must have scans and at least one expected output')
            for i, scan in enumerate(scans):
                values = program.scan(scan.get('inputs', {}), scan.get('dt_ms', scan_ms))
                trace.append(values)
                for name, expected in scan.get('expect', {}).items():
                    actual = values.get(name.upper())
                    valid = (type(actual) is type(expected) and actual == expected)
                    if type(expected) in (float,int) and type(actual) in (float,int):
                        valid = math.isclose(actual, expected, abs_tol=scan.get('tolerance', 1e-6), rel_tol=1e-6)
                    if not valid:
                        failures.append({'scan': i, 'variable': name, 'expected': expected, 'actual': actual})
            status = 'failed' if failures else 'passed'
        except Unsupported as exc:
            status = 'unsupported'
            failures.append({'error': str(exc)})
        except (STError, ArithmeticError, TypeError, KeyError, IndexError, RecursionError) as exc:
            status = 'failed'
            failures.append({'error': str(exc)})
        results.append({'name': case.get('name', 'unnamed'), 'status': status,
                        'failures': failures, 'trace': trace})
    passed = sum(r['status'] == 'passed' for r in results)
    status = 'passed' if results and passed == len(results) else (
        'unsupported' if any(r['status'] == 'unsupported' for r in results) else 'failed')
    return {'status': status, 'engine': 'st-subset-scan-simulator', 'is_plc_runtime': False,
            'passed': passed, 'total': len(results), 'cases': results}
