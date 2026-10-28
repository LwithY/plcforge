"""Finite-trace LTL monitoring. Results are observations, never formal proofs."""
from __future__ import annotations

import re


class SpecError(ValueError):
    pass


def parse(formula: str):
    pattern = re.compile(r'\s+|->|>=|<=|!=|<>|&&|\|\||[A-Za-z_][A-Za-z_0-9]*|\d+(?:\.\d+)?|[()!&|=<>+\-*/]')
    tokens, pos = [], 0
    while pos < len(formula):
        match = pattern.match(formula, pos)
        if not match:
            raise SpecError('Unsupported LTL syntax: ' + formula[pos:pos+25])
        if not match.group().isspace():
            tokens.append(match.group())
        pos = match.end()
    cursor = 0
    prec = {'->':1, '|':2, '||':2, '&':3, '&&':3, 'U':4, '=':5, '!=':5,
            '<>':5, '<':5, '>':5, '<=':5, '>=':5, '+':6, '-':6, '*':7, '/':7}
    def expr(minimum=0):
        nonlocal cursor
        if cursor >= len(tokens):
            raise SpecError('Incomplete LTL formula')
        token = tokens[cursor]
        cursor += 1
        if token in ('!', 'G', 'F', 'X', '-'):
            left = (token, expr(8))
        elif token == '(':
            left = expr()
            if cursor >= len(tokens) or tokens[cursor] != ')':
                raise SpecError('Missing LTL closing parenthesis')
            cursor += 1
        elif re.fullmatch(r'\d+(?:\.\d+)?', token):
            left = ('constant', float(token))
        elif token.lower() in ('true','false'):
            left = ('constant', token.lower() == 'true')
        elif re.fullmatch(r'[A-Za-z_]\w*', token):
            left = ('variable', token.upper())
        else:
            raise SpecError('Invalid LTL atom: ' + token)
        while cursor < len(tokens) and tokens[cursor] in prec and prec[tokens[cursor]] >= minimum:
            op = tokens[cursor]
            cursor += 1
            left = (op, left, expr(prec[op] + (0 if op in ('->','U') else 1)))
        return left
    ast = expr()
    if cursor != len(tokens):
        raise SpecError('Unexpected LTL tokens: ' + ' '.join(tokens[cursor:]))
    return ast


def conjunction(a, b):
    return False if a is False or b is False else None if a is None or b is None else bool(a and b)


def disjunction(a, b):
    return True if a is True or b is True else None if a is None or b is None else bool(a or b)


def evaluate(node, trace: list[dict], i: int):
    if i >= len(trace):
        return None
    op = node[0]
    if op == 'constant': return node[1]
    if op == 'variable':
        if node[1] not in trace[i]:
            raise SpecError('Unknown trace proposition: ' + node[1])
        return trace[i][node[1]]
    if op == 'X': return evaluate(node[1], trace, i+1)
    if op in ('G','F'):
        values = [evaluate(node[1], trace, j) for j in range(i, len(trace))]
        if op == 'G':
            return False if False in values else None if None in values else True
        return True if True in values else None
    if op == 'U':
        preceding = True
        for j in range(i, len(trace)):
            right = evaluate(node[2], trace, j)
            candidate = conjunction(preceding, right)
            if candidate is True:
                return True
            preceding = conjunction(preceding, evaluate(node[1], trace, j))
            if preceding is False:
                return False if candidate is False else None
        return None
    a = evaluate(node[1], trace, i)
    if op == '!': return None if a is None else not a
    if op == '-' and len(node) == 2: return None if a is None else -a
    b = evaluate(node[2], trace, i)
    if op in ('&','&&'): return conjunction(a,b)
    if op in ('|','||'): return disjunction(a,b)
    if op == '->': return disjunction(None if a is None else not a,b)
    if a is None or b is None: return None
    if op == '=': return a == b
    if op in ('!=','<>'): return a != b
    if op == '<': return a < b
    if op == '>': return a > b
    if op == '<=': return a <= b
    if op == '>=': return a >= b
    if op == '+': return a+b
    if op == '-': return a-b
    if op == '*': return a*b
    if op == '/': return a/b
    raise SpecError('Unsupported LTL operator: ' + op)


def monitor(specs: list[dict], test_result: dict) -> dict:
    observations = []
    for case in test_result.get('cases', []):
        for spec in specs:
            try:
                value = evaluate(parse(spec['formula']), case.get('trace', []), 0)
                status = 'violated' if value is False else 'inconclusive' if value is None else 'no_violation_observed'
                observation = {'spec': spec['id'], 'case': case['name'], 'status': status}
            except (SpecError, ArithmeticError, TypeError) as exc:
                observation = {'spec': spec.get('id'), 'case': case['name'], 'status': 'unsupported', 'error': str(exc)}
            observations.append(observation)
    return {'engine': 'finite-trace-ltl-monitor', 'is_formal_verification': False,
            'status': 'violated' if any(o['status'] == 'violated' for o in observations) else
                      'unsupported' if any(o['status'] == 'unsupported' for o in observations) else
                      'inconclusive' if not observations or any(o['status'] == 'inconclusive' for o in observations) else
                      'no_violation_observed',
            'observations': observations,
            'limitation': 'Only supplied finite scan traces are checked; no exhaustive state exploration.'}
