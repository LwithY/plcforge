"""Session-cumulative accounting, separate from provider usage and cash billing."""
from __future__ import annotations

import json
import math

from .provider import ModelError


class BudgetExceeded(ModelError):
    pass


POLICY_FIELDS = ('total_token_budget', 'request_token_budget', 'max_output_tokens',
                 'min_output_tokens', 'input_estimate_safety_factor')


def initialize(state, config, replace=False):
    requested = {k:getattr(config, k) for k in POLICY_FIELDS}
    saved = state.get('budget_policy')
    if saved and not replace:
        for key, value in saved.items():
            setattr(config, key, value)
    elif saved and saved != requested:
        state.setdefault('budget_changes', []).append({'previous':saved, 'replacement':requested,
            'consumed_tokens':state.get('estimated_budget_tokens', 0), 'explicit_replacement':True})
    state['budget_policy'] = {k:getattr(config, k) for k in POLICY_FIELDS}
    state.setdefault('usage_records', [])
    state.setdefault('cached_input_tokens', 0)


def preflight(state, config, messages, schemas):
    body = json.dumps({'messages':messages, 'tools':schemas, 'model':config.model}, ensure_ascii=False)
    estimate = math.ceil(len(body.encode('utf-8'))/3*config.input_estimate_safety_factor)+64
    remaining = max(0, config.total_token_budget-state.get('estimated_budget_tokens', 0))
    ceiling = min(remaining, config.request_token_budget or remaining)
    output = min(config.max_output_tokens, max(0, ceiling-estimate))
    result = {'estimated_input_tokens':estimate, 'reserved_output_tokens':output,
              'remaining_session_tokens':remaining, 'estimator':'utf8-bytes/3 with safety factor; approximate',
              'allowed':output >= min(config.min_output_tokens, config.max_output_tokens),
              'session_policy':dict(state['budget_policy'])}
    if not result['allowed']:
        result['reason'] = 'Estimated input plus minimum output reserve exceeds remaining session/request budget'
    return result


def account(state, estimate, usage=None, *, failed_attempt=False, usage_complete=True):
    usage = usage or {}
    known = all(type(usage.get(k)) is int and usage[k] >= 0 for k in ('prompt_tokens','completion_tokens','total_tokens'))
    charged = usage['total_tokens'] if known else estimate['estimated_input_tokens']+estimate['reserved_output_tokens']
    cached = (usage.get('prompt_tokens_details') or {}).get('cached_tokens', usage.get('prompt_cache_hit_tokens', 0)) or 0
    record = {'raw_api_usage':usage, 'estimated_input_tokens':estimate['estimated_input_tokens'],
              'output_reservation':estimate['reserved_output_tokens'], 'budget_charge_tokens':charged,
              'charge_basis':'provider_reported' if known else 'conservative_reservation_estimate',
              'failed_attempt':failed_attempt, 'usage_complete':known and usage_complete,
              'cash_cost':None, 'cash_cost_status':'not_estimated; consult provider bill', 'cached_input_tokens':cached}
    state['estimated_budget_tokens'] = state.get('estimated_budget_tokens', 0)+charged
    for key in ('prompt_tokens','completion_tokens','total_tokens'):
        value = usage.get(key)
        if type(value) is int and value >= 0:
            state['usage'][key] = state['usage'].get(key, 0)+value
    state['cached_input_tokens'] = state.get('cached_input_tokens', 0)+cached
    if not known or not usage_complete:
        state['usage_missing'] = True
    state.setdefault('usage_records', []).append(record)
    return record
