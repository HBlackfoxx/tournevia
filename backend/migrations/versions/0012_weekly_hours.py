"""Vehicles keep weekly working hours and dated exceptions instead of one dated window.

A company vehicle's saved window becomes an exception with those hours on its date; no weekly
hours are invented for it, so an administrator sets them once. Vehicles of a fictional sample
work their hours every day, so the sample can be planned whenever it is opened.
"""
from datetime import date, datetime, timedelta
import json
from zoneinfo import ZoneInfo

from alembic import op
import sqlalchemy as sa

revision = '0012_weekly_hours'
down_revision = '0011_depot_address'
branch_labels = None
depends_on = None

PARIS = ZoneInfo('Europe/Paris')
DAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')


def rows(connection):
    return connection.execute(sa.text(
        'SELECT v.id, v.data, o.is_demo FROM vehicles v JOIN organizations o ON o.id = v.organization_id')).all()


def save(connection, key, data):
    connection.execute(sa.text('UPDATE vehicles SET data = CAST(:data AS jsonb) WHERE id = :id'),
                       {'id': key, 'data': json.dumps(data)})


def upgrade():
    connection = op.get_bind()
    for key, data, sample in rows(connection):
        window = data.pop('available_window', None)
        if window is None:
            continue
        start, end = (datetime.fromisoformat(window[edge]).astimezone(PARIS) for edge in ('start', 'end'))
        hours = {'start': start.strftime('%H:%M'), 'end': end.strftime('%H:%M')}
        if sample:
            data['hours'], data['exceptions'] = {day: hours for day in DAYS}, []
        else:
            data['hours'] = {day: None for day in DAYS}
            data['exceptions'] = [{'date': start.date().isoformat(), 'hours': hours}]
        save(connection, key, data)


def downgrade():
    # One dated window per vehicle again: its latest exception with hours, or else its first
    # weekday with hours in the week of 5 January 2026.
    connection = op.get_bind()
    for key, data, _ in rows(connection):
        hours, exceptions = data.pop('hours', None) or {}, data.pop('exceptions', None) or []
        dated = [e for e in exceptions if e.get('hours')]
        if dated:
            day, chosen = date.fromisoformat(dated[-1]['date']), dated[-1]['hours']
        else:
            index = next((i for i, name in enumerate(DAYS) if hours.get(name)), 0)
            day, chosen = date(2026, 1, 5) + timedelta(days=index), hours.get(DAYS[index]) or {'start': '08:00', 'end': '17:00'}
        data['available_window'] = {edge: datetime.combine(day, datetime.strptime(chosen[edge], '%H:%M').time(), PARIS).isoformat()
                                    for edge in ('start', 'end')}
        save(connection, key, data)
