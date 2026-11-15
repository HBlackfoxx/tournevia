"""Second-pass estimates must not hide other warnings or fabricate road cells."""
from copy import deepcopy
import json

import pytest

from app.routing import Deadline, EngineError, Valhalla, parse_matrix


def response():
    return {'algorithm': 'costmatrix', 'units': 'kilometers',
            'warnings': [{'code': 400, 'text': 'CostMatrix turned off destination-only on a second pass for connections: 1,2'}],
            'sources_to_targets': {'durations': [[0, 12], [None, 0]], 'distances': [[0, .1], [None, 0]]}}


def request(monkeypatch, data, action='sources_to_targets'):
    monkeypatch.setattr(Valhalla, 'exchange', lambda self, action, body, deadline: (200, json.dumps(data).encode()))
    return Valhalla('http://unused', 'test').post(action, {'sources': [{}, {}], 'targets': [{}, {}]}, Deadline.after(2))


def test_second_pass_notice_preserves_finite_estimates_and_remaining_nulls(monkeypatch):
    data = response(); original = deepcopy(data)
    parsed = request(monkeypatch, data)
    assert parsed == original
    matrix = parse_matrix(parsed, 2)
    assert matrix.durations == [[0, 12], [None, 0]]
    assert matrix.distances == [[0, 100], [None, 0]]


@pytest.mark.parametrize('change', ['route', 'algorithm', 'code', 'code_type', 'unknown_extra',
    'duplicate_notice', 'text', 'index', 'duplicate_index', 'negative_index', 'malformed_index',
    'order', 'empty', 'object', 'error'])
def test_other_warnings_and_invalid_recovery_notices_fail(monkeypatch, change):
    data = response(); notice = data['warnings'][0]; action = 'sources_to_targets'
    if change == 'route': action = 'route'
    if change == 'algorithm': data['algorithm'] = 'timedistancematrix'
    if change == 'code': notice['code'] = 210
    if change == 'code_type': notice['code'] = 400.0
    if change == 'unknown_extra': data['warnings'].append({'code': 210, 'text': 'clamped'})
    if change == 'duplicate_notice': data['warnings'].append(deepcopy(notice))
    if change == 'text': notice['text'] = 'Destination recovery succeeded'
    suffix = {'index': '1,4', 'duplicate_index': '1,1', 'negative_index': '-1',
              'malformed_index': '0,'+'9'*5000, 'order': '2,1', 'empty': ''}.get(change)
    if suffix is not None: notice['text'] = notice['text'].split(': ')[0]+': '+suffix
    if change == 'object': data['warnings'] = notice
    if change == 'error': data['error'] = 'No path'
    with pytest.raises(EngineError): request(monkeypatch, data, action)


def test_second_pass_notice_does_not_excuse_malformed_matrix(monkeypatch):
    data = response(); data['sources_to_targets']['distances'][1][0] = 0
    with pytest.raises(EngineError): parse_matrix(request(monkeypatch, data), 2)
