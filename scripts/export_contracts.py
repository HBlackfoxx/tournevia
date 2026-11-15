"""Generate the API contract, every JSON schema and the list of server messages from the application code."""
import argparse
import ast
import json
from pathlib import Path
import re
import sys

from pydantic.json_schema import models_json_schema

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from app.auth import CreateUser
from app.config import Settings
from app.fleet import DepotInput
from app.imports import HEADERS, OPTIONAL_HEADERS
from app.main import create_app
from app.jobs import JobRecord, OptimizationRequest, PlanRecord
from app.edits import EditRequest, FinalizeRequest
from app.messages import BOUNDS, FIXED
from app.planning import CustomerInput, OrderInput, SyntheticInput, SyntheticDraft, VehicleSource

SCHEMA_DIALECT = 'https://json-schema.org/draft/2020-12/schema'
SCHEMA_BASE = 'https://schemas.invalid/tournevia/'


# Messages these modules produce are seen only by operators (CLIs, metrics, startup settings).
OPERATOR_ONLY = {'manage_users.py', 'retention.py', 'monitoring.py', 'config.py', 'database_roles.py'}
# Position of the message among each error constructor's arguments.
MESSAGE_ARGUMENT = {'DataError': 2, 'fail_job': 2, 'fail_work': 3, 'EngineError': 0, 'CellError': 1, 'CalculationFailure': 1}


def literal(node: ast.expr) -> str | None:
    """A message literal, or the fixed start of one built with `+`."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return literal(node.left)
    return None


def sentences(node: ast.expr) -> list[str]:
    """Fixed sentences inside a formatted message, e.g. the advice after a sentence naming records."""
    if isinstance(node, ast.JoinedStr):
        text = ''.join(v.value if isinstance(v, ast.Constant) else '\0' for v in node.values)
        parts = [s.strip() for part in text.split('\0') for s in re.split(r'(?<=[.!?])\s+', part)]
        return [s for s in parts if s[:1].isupper() and s.endswith('.')]
    return []


def server_messages() -> list[str]:
    """Every fixed server message the interface may show; the web app requires a French translation for each."""
    found = {t for t, _ in BOUNDS.values()} | set(FIXED.values())
    for path in sorted((ROOT / 'backend/app').glob('*.py')):
        if path.name in OPERATOR_ONLY:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.Call):
                name = getattr(node.func, 'id', getattr(node.func, 'attr', None))
                if name in MESSAGE_ARGUMENT and len(node.args) > MESSAGE_ARGUMENT[name]:
                    found.add(literal(node.args[MESSAGE_ARGUMENT[name]]))
                    found.update(sentences(node.args[MESSAGE_ARGUMENT[name]]))
                if name in ('RowError', 'RowWarning'):
                    found.update(literal(k.value) for k in node.keywords if k.arg in ('message', 'template'))
            elif isinstance(node, ast.Dict):
                found.update(literal(v) for k, v in zip(node.keys, node.values)
                             if isinstance(k, ast.Constant) and k.value == 'detail')
            elif isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and path.name != 'routing.py'                     and getattr(node.exc.func, 'id', None) == 'ValueError' and node.exc.args:
                # Model rules reach users as field errors; routing.py raises them only for startup settings.
                found.add(literal(node.exc.args[0]))
            elif isinstance(node, ast.Assign) and any(getattr(t, 'id', None) == 'template' for t in node.targets):
                found.add(literal(node.value))
            elif isinstance(node, ast.Assign) and len(node.targets) == 1 and getattr(node.targets[0], 'id', '').endswith(('_FAILURE', '_MESSAGE')):
                found.add(literal(node.value))
    return sorted(m for m in found if m)


LIMIT_KEYS = ('minimum', 'maximum', 'minLength', 'maxLength', 'pattern', 'maxItems')


def field_limits(model) -> dict[str, dict]:
    """Form limits by dotted field path, so the interface enforces the API's own bounds."""
    schema = model.model_json_schema()
    definitions, found = schema.get('$defs', {}), {}

    def walk(node: dict, path: str) -> None:
        if '$ref' in node:
            node = definitions[node['$ref'].rsplit('/', 1)[-1]]
        for option in node.get('anyOf', []):
            walk(option, path)
        if 'properties' in node:
            for name, sub in node['properties'].items():
                walk(sub, f'{path}.{name}' if path else name)
            return
        rule = {k: node[k] for k in LIMIT_KEYS if k in node}
        if node.get('type') == 'array':  # Equipment codes: the list bound and each code's pattern.
            rule.update({k: v for k, v in node.get('items', {}).items() if k in LIMIT_KEYS})
        if rule:
            found[path] = {**found.get(path, {}), **rule}

    walk(schema, '')
    return found


def published(schema: dict, name: str) -> dict:
    schema.update({'$schema': SCHEMA_DIALECT, '$id': f'{SCHEMA_BASE}{name}.schema.json'})
    return schema


def documents() -> dict[Path, dict]:
    # Explicit settings make export independent of a developer's .env or APP_MODE.
    app = create_app(Settings(_env_file=None, app_mode='local_demo', public_origin='http://localhost:8000'))
    output = {ROOT / 'backend/openapi.json': app.openapi(),
              ROOT / 'backend/spike-input.schema.json': SyntheticInput.model_json_schema(),
              ROOT / 'backend/spike-output.schema.json': SyntheticDraft.model_json_schema()}
    # Source inputs share their nested definitions through common.schema.json,
    # which holds exactly the definitions these schemas reference.
    sources = [('depot', DepotInput), ('vehicle', VehicleSource), ('order', OrderInput)]
    shared = set()
    for name, model in sources:
        schema = model.model_json_schema(ref_template='common.schema.json#/$defs/{model}')
        shared.update(schema.pop('$defs', {}))
        output[ROOT / f'schemas/{name}.schema.json'] = published(schema, name)
    _, definitions = models_json_schema([(model, 'validation') for _, model in sources], ref_template='#/$defs/{model}')
    output[ROOT / 'schemas/common.schema.json'] = {
        '$schema': SCHEMA_DIALECT, '$id': f'{SCHEMA_BASE}common.schema.json', 'title': 'Common contract definitions',
        '$defs': {name: definitions['$defs'][name] for name in sorted(shared)}}
    for name, model in [('job', JobRecord), ('optimization-request', OptimizationRequest), ('plan-edit', EditRequest),
                        ('finalize-request', FinalizeRequest), ('route-plan', PlanRecord)]:
        output[ROOT / f'schemas/{name}.schema.json'] = published(model.model_json_schema(), name)
    output[ROOT / 'web/src/locales/server-messages.json'] = server_messages()
    output[ROOT / 'web/src/field-limits.json'] = {name: field_limits(model) for name, model in [
        ('depot', DepotInput), ('vehicles', VehicleSource), ('orders', OrderInput), ('customers', CustomerInput),
        ('users', CreateUser)]}
    # The import's exact columns, so the interface can show the header row and offer a template file.
    output[ROOT / 'web/src/csv-columns.json'] = {kind: {'required': HEADERS[kind], 'optional': OPTIONAL_HEADERS[kind]}
                                                 for kind in ('orders', 'vehicles')}
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    output = documents()
    for path, document in output.items():
        rendered = json.dumps(document, indent=2, ensure_ascii=False) + '\n'
        if args.check:
            if not path.exists() or path.read_text(encoding='utf-8') != rendered:
                raise SystemExit(f'Contract drift: {path.relative_to(ROOT)}; run scripts/export_contracts.py')
        else:
            path.write_text(rendered, encoding='utf-8', newline='\n')
    # A schema written by hand would become a second, unchecked authority.
    unexpected = sorted(path.name for path in (ROOT / 'schemas').glob('*.json') if path not in output)
    if unexpected:
        raise SystemExit(f'Schemas not generated from application models: {", ".join(unexpected)}')
    print('API, JSON schema and server message contracts match the application.' if args.check else 'Exported API, JSON schema and server message contracts.')


if __name__ == '__main__':
    main()
