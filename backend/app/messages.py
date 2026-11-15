"""Plain, translatable wording for model validation errors.

Pydantic's messages are English and embed values. Each error here becomes a fixed English
`template` with `{0}` placeholders plus its `values`, so the interface can translate it.
"""
from typing import Any

DATETIME = 'Enter a date and time with its UTC offset, for example 2026-09-23T08:00:00+02:00.'
DATE = 'Enter a date such as 2026-09-23.'
BOUNDS = {
    'less_than_equal': ('Use a value of at most {0}.', 'le'),
    'greater_than_equal': ('Use a value of at least {0}.', 'ge'),
    'less_than': ('Use a value below {0}.', 'lt'),
    'greater_than': ('Use a value above {0}.', 'gt'),
    'string_too_long': ('Use at most {0} characters.', 'max_length'),
    'string_too_short': ('Use at least {0} characters.', 'min_length'),
    'too_long': ('Use at most {0} items.', 'max_length'),
    'too_short': ('Use at least {0} items.', 'min_length'),
}
FIXED = {
    'missing': 'This value is required.',
    'datetime_parsing': DATETIME, 'datetime_from_date_parsing': DATETIME, 'datetime_type': DATETIME, 'timezone_aware': DATETIME,
    'date_parsing': DATE, 'date_from_datetime_parsing': DATE, 'date_type': DATE,
    'float_parsing': 'Enter a number.', 'float_type': 'Enter a number.', 'finite_number': 'Enter a number.',
    'int_parsing': 'Enter a whole number.', 'int_type': 'Enter a whole number.', 'int_from_float': 'Enter a whole number.',
    'bool_parsing': 'Use true or false.', 'bool_type': 'Use true or false.',
    'string_pattern_mismatch': 'Use only the allowed characters.',
    'literal_error': 'Choose one of the allowed values.', 'enum': 'Choose one of the allowed values.',
    'extra_forbidden': 'This field is not allowed.',
}
TEMPLATES = sorted({t for t, _ in BOUNDS.values()} | set(FIXED.values()))


def plain(error: dict[str, Any]) -> tuple[str, str, list[str]]:
    """English message, its template and the template values for one Pydantic error."""
    kind, ctx = error.get('type'), error.get('ctx') or {}
    if kind in BOUNDS and BOUNDS[kind][1] in ctx:
        template, key = BOUNDS[kind]
        bound = ctx[key]
        value = str(int(bound)) if isinstance(bound, float) and bound.is_integer() else str(bound)
        return template.format(value), template, [value]
    if kind in FIXED:
        return FIXED[kind], FIXED[kind], []
    if kind in ('value_error', 'assertion_error') and 'error' in ctx:
        # The application's own validator messages are fixed sentences.
        text = str(ctx['error'])
        return text, text, []
    return str(error.get('msg', '')), str(error.get('msg', '')), []
