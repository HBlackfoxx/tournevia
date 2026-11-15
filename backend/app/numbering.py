"""Delivery numbers: a company's format for new delivery identifiers, and the next free number.

A format is literal text with date parts of the delivery's service date and one sequence number,
for example ``LIV-{YYYY}-{N:4}`` gives LIV-2026-0001. The next number is one more than the highest
existing identifier written in the same format (and at least the company's starting number), so it
follows imports, hand-typed identifiers and earlier numbers without a separate counter. With a date
part, numbering starts again in each year, month or day. Numbers are only final when a delivery is
saved: callers compute them under the company lock.
"""
from collections import Counter
from datetime import date
import re
from uuid import UUID

from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.planning import Model
from app.records import Order

TOKEN = re.compile(r'\{(YYYY|YY|MM|DD|N(?::([1-9]))?)\}')
# Literal text between parts: letters, digits and common separators, never braces.
LITERAL = re.compile(r'[A-Za-z0-9 ._/-]*')
MAX_FORMAT = 40


class NumberingSettings(Model):
    format: str | None = Field(None, max_length=MAX_FORMAT, description=(
        'Identifier format for new deliveries, or null for none. Literal letters, digits and . _ / - '
        'with date parts {YYYY} {YY} {MM} {DD} of the service date and one number {N} or {N:4} '
        '(zero-padded to 4 digits), e.g. LIV-{YYYY}-{N:4}.'))
    start: int = Field(1, ge=1, le=999_999_999, description='The lowest number to give, e.g. to continue an earlier system.')

    @field_validator('format')
    @classmethod
    def valid_format(cls, value: str | None) -> str | None:
        if value is None or value == '':
            return None
        if len(re.findall(r'\{N(?::[1-9])?\}', value)) != 1:
            raise ValueError('A delivery number format needs exactly one number part, {N} or {N:4}.')
        if not all(LITERAL.fullmatch(part) for part in TOKEN.split(value)[::3]):
            raise ValueError('Use letters, digits, spaces and . _ / - between the parts of a number format.')
        return value


class NumberingRecord(NumberingSettings):
    next: str | None = Field(None, description='The number a delivery saved today would receive.')
    suggestion: str | None = Field(None, description='A format matching most existing delivery identifiers.')


def split(template: str, day: date) -> tuple[str, str, int]:
    """The text before and after the number on `day`, and its zero-padding."""
    parts = {'YYYY': f'{day.year:04d}', 'YY': f'{day.year % 100:02d}', 'MM': f'{day.month:02d}', 'DD': f'{day.day:02d}'}
    head, tail, digits, seen = [], [], 1, False
    position = 0
    for match in TOKEN.finditer(template):
        (tail if seen else head).append(template[position:match.start()])
        if match.group(1).startswith('N'):
            seen, digits = True, int(match.group(2) or 1)
        else:
            (tail if seen else head).append(parts[match.group(1)])
        position = match.end()
    tail.append(template[position:])
    return ''.join(head), ''.join(tail), digits


# Numbers longer than the largest starting number are not followed: one mistyped identifier with a
# very long number would otherwise make every later number as long, up to identifiers too long to save.
MAX_DIGITS = 9


def highest(values, head: str, tail: str) -> int:
    """The highest number among these identifiers written as `head`, a number and `tail`; 0 if none."""
    pattern = re.compile(re.escape(head) + rf'([0-9]{{1,{MAX_DIGITS}}})' + re.escape(tail))
    return max((int(m.group(1)) for value in values if (m := pattern.fullmatch(value))), default=0)


def next_identifier(session: Session, owner: UUID, settings: NumberingSettings, day: date,
                    taken: set[str] = frozenset(), saved: dict[tuple[str, str], int] | None = None) -> str:
    """The next free identifier for a delivery on `day`, skipping `taken` ones not yet saved.

    `saved` keeps the highest saved number per format text between calls, so numbering a whole
    import reads the saved identifiers once per date rather than once per row."""
    head, tail, digits = split(settings.format, day)
    if saved is None or (head, tail) not in saved:
        found = highest(session.scalars(select(Order.identifier).where(
            Order.organization_id == owner, Order.identifier.startswith(head, autoescape=True))), head, tail)
        if saved is None:
            saved = {}
        saved[(head, tail)] = found
    number = max(saved[(head, tail)] + 1, highest(taken, head, tail) + 1, settings.start)
    return f'{head}{number:0{digits}d}{tail}'


def suggest(session: Session, owner: UUID) -> str | None:
    """The format most existing identifiers share (text then a number), to continue them."""
    shapes = Counter()
    # Example deliveries are fictional and removed with the examples; their identifiers suggest nothing.
    for value in session.scalars(select(Order.identifier).where(Order.organization_id == owner, Order.example.is_(False))
                                 .limit(5000)):
        if (m := re.fullmatch(r'([A-Za-z0-9 ._/-]*?)([0-9]+)', value)) and len(m.group(1)) <= MAX_FORMAT - 6:
            shapes[(m.group(1), len(m.group(2)))] += 1
    if not shapes:
        return None
    (prefix, digits), _ = shapes.most_common(1)[0]
    return f'{prefix}{{N:{min(digits, 9)}}}'
