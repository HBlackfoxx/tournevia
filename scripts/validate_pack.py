#!/usr/bin/env python3
"""Check documentation, generated contract files and examples offline.

This needs only requirements-docs.txt and never imports the application. The
contracts themselves come from code (scripts/export_contracts.py --check), and
backend/tests/test_examples.py checks the examples with the application models.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from copy import deepcopy
from pathlib import Path
from urllib.parse import unquote, urlparse

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
DIMENSIONS = [
    "weight_kg", "volume_dm3", "pallet_eur_units",
    "pallet_ind_units", "loading_length_mm",
]
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
EXAMPLES = {
    "depot": "depot", "vehicle": "vehicle", "vehicle-van": "vehicle",
    "order": "order", "order-second": "order",
    "optimization-request": "optimization-request", "edit-plan": "plan-edit",
    "finalize-request": "finalize-request", "job": "job", "route-plan": "route-plan",
    "vehicle-with-limits": "vehicle", "order-priority": "order",
    "optimization-cost-request": "optimization-request",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def pointer(document, fragment: str):
    if not fragment:
        return document
    require(fragment.startswith("/"), f"Unsupported reference fragment: {fragment}")
    value = document
    for part in unquote(fragment[1:]).split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def check_references(documents: dict[Path, dict], schema_ids: dict[str, Path]) -> int:
    count = 0
    for path, document in documents.items():
        for node in walk(document):
            if "$ref" not in node:
                continue
            base, _, fragment = node["$ref"].partition("#")
            if not base:
                target = path
            elif base in schema_ids:
                target = schema_ids[base]
            else:
                require(not urlparse(base).scheme, f"Unbundled external reference: {base}")
                target = (path.parent / unquote(base)).resolve()
            require(target.is_relative_to(ROOT), f"Reference leaves pack: {target}")
            require(target in documents, f"Missing referenced document: {target}")
            pointer(documents[target], fragment)
            count += 1
    return count


def check_links() -> int:
    count = 0
    for path in ROOT.rglob("*.md"):
        if path.is_relative_to(ROOT / 'deploy' / 'runtime'):
            continue
        if any(part in {'.git', '.venv', 'node_modules', '.pytest_cache', '.pytest-tmp'}
               for part in path.relative_to(ROOT).parts):
            continue
        text = path.read_text(encoding="utf-8")
        require(text.count("```") % 2 == 0, f"Unclosed code fence: {path.name}")
        for target in re.findall(r"\[[^\]\n]*\]\(([^)\n]+)\)", text):
            target = target.split("#", 1)[0]
            if not target or urlparse(target).scheme:
                continue
            resolved = (path.parent / unquote(target)).resolve()
            require(resolved.is_relative_to(ROOT), f"Link leaves pack: {target}")
            require(resolved.exists(), f"Broken link in {path.name}: {target}")
            count += 1
    return count


def boolean(value: str) -> bool:
    require(value in {"true", "false"}, f"Invalid CSV boolean: {value}")
    return value == "true"


def check_csv(validators: dict, examples: dict) -> int:
    count = 0
    with (ROOT / "examples/vehicles.csv").open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        expected_headers = [
            "fleet_number", "active", "height_mm", "width_mm", "length_mm",
            "gross_weight_kg", "axle_load_kg", "axle_count", *DIMENSIONS,
            "skills", *DAYS, "fixed_minor",
            "per_km_minor", "per_hour_minor",
        ]
        require(reader.fieldnames == expected_headers, "Vehicle CSV headers differ from contract")
        rows = list(reader)
        require(len(rows) == 2, "Expected two vehicle example rows")
        for row, example_name in zip(rows, ["vehicle", "vehicle-van"]):
            value = {
                "fleet_number": row["fleet_number"], "active": boolean(row["active"]),
                "routing": {key: int(row[key]) for key in expected_headers[2:8]},
                "capacity": {key: int(row[key]) for key in DIMENSIONS},
                "skills": row["skills"].split("|") if row["skills"] else [],
                "hours": {day: dict(zip(("start", "end"), row[day].split("-"))) if row[day] else None for day in DAYS},
                "cost": {"currency": "EUR", **{key: int(row[key]) for key in ["fixed_minor", "per_km_minor", "per_hour_minor"]}},
            }
            validators["vehicle"].validate(value)
            require(value == examples[example_name], f"CSV and JSON disagree: {example_name}")
            count += 1
    with (ROOT / "examples/orders.csv").open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        expected_headers = [
            "external_id", "service_date", "active", "address", "latitude", "longitude",
            "time_window_start", "time_window_end", "service_seconds", *DIMENSIONS,
            "required_skills", "instructions",
        ]
        require(reader.fieldnames == expected_headers, "Order CSV headers differ from contract")
        rows = list(reader)
        require(len(rows) == 2, "Expected two order example rows")
        for row, example_name in zip(rows, ["order", "order-second"]):
            value = {
                "external_id": row["external_id"], "service_date": row["service_date"],
                "active": boolean(row["active"]), "address": row["address"],
                "location": {"latitude": float(row["latitude"]), "longitude": float(row["longitude"]), "reviewed": False},
                "time_window": {"start": row["time_window_start"], "end": row["time_window_end"]},
                "service_seconds": int(row["service_seconds"]),
                "demand": {key: int(row[key]) for key in DIMENSIONS},
                "required_skills": row["required_skills"].split("|") if row["required_skills"] else [],
                "instructions": row["instructions"],
            }
            validators["order"].validate(value)
            expected = deepcopy(examples[example_name])
            expected["location"]["reviewed"] = False
            require(value == expected, f"CSV and JSON disagree: {example_name}")
            count += 1
    return count


def negative_checks(validators: dict, examples: dict) -> int:
    """Only rules the generated schemas express; the application checks the rest."""
    cases = []
    value = deepcopy(examples["vehicle"]); value["capacity"]["weight_kg"] = -1
    cases.append(("vehicle", value, "negative capacity"))
    value = deepcopy(examples["vehicle"]); value["unused_future_feature"] = True
    cases.append(("vehicle", value, "unexpected field"))
    value = deepcopy(examples["vehicle"]); del value["routing"]["height_mm"]
    cases.append(("vehicle", value, "missing truck field"))
    value = deepcopy(examples["order"]); value["location"]["latitude"] = 91
    cases.append(("order", value, "invalid coordinate"))
    value = deepcopy(examples["order"]); value["time_window"]["start"] = "2026-09-23T08:00:00"
    cases.append(("order", value, "naive timestamp"))
    value = deepcopy(examples["optimization-request"]); value["order_ids"].append(value["order_ids"][0])
    cases.append(("optimization-request", value, "duplicate order ID"))
    value = deepcopy(examples["optimization-request"]); value["solver_time_limit_seconds"] = 61
    cases.append(("optimization-request", value, "excess solver budget"))
    value = deepcopy(examples["finalize-request"]); value["accept_unassigned"] = "yes"
    cases.append(("finalize-request", value, "non-boolean unassigned acceptance"))
    value = deepcopy(examples["job"]); value["plan_id"] = None
    cases.append(("job", value, "success without result"))
    value = deepcopy(examples["order-priority"]); value["priority"] = 101
    cases.append(("order", value, "excess order priority"))
    value = deepcopy(examples["order-priority"]); value["priority"] = -1
    cases.append(("order", value, "negative order priority"))
    value = deepcopy(examples["vehicle-with-limits"]); value["limits"]["max_stops"] = 0
    cases.append(("vehicle", value, "zero route stop limit"))
    value = deepcopy(examples["vehicle-with-limits"]); value["limits"]["max_distance_m"] = -1
    cases.append(("vehicle", value, "negative distance limit"))
    value = deepcopy(examples["vehicle-with-limits"]); value["limits"]["max_driving_seconds"] = 0
    cases.append(("vehicle", value, "zero driving limit"))
    value = deepcopy(examples["optimization-request"]); value["objective"] = "ALL_IN_COST"
    cases.append(("optimization-request", value, "unsupported objective"))
    value = deepcopy(examples["order"]); value["organization_id"] = examples["route-plan"]["organization_id"]
    cases.append(("order", value, "client-assigned organization"))
    value = deepcopy(examples["route-plan"]); value["result"]["toll_cost_minor"] = 0
    cases.append(("route-plan", value, "unknown toll converted to zero"))
    value = deepcopy(examples["route-plan"]); value["result"]["total_cost_minor"] = 0
    cases.append(("route-plan", value, "all-in total without tolls"))
    value = deepcopy(examples["route-plan"]); del value["organization_id"]
    cases.append(("route-plan", value, "missing plan owner"))
    value = deepcopy(examples["route-plan"]); value["version"] = 0
    cases.append(("route-plan", value, "invalid edit version"))
    value = deepcopy(examples["route-plan"]); value["unused_future_feature"] = True
    cases.append(("route-plan", value, "unexpected plan field"))
    value = deepcopy(examples["route-plan"]); value["result"]["operational_ready"] = True
    cases.append(("route-plan", value, "draft result marked operationally ready"))
    value = deepcopy(examples["route-plan"]); value["result"]["routes"][0]["stops"][0]["load_after"][0] = -1
    cases.append(("route-plan", value, "negative remaining load"))
    value = deepcopy(examples["route-plan"]); value["result"]["routes"][0]["legs"][0]["geometry"] = [[2.375, 48.835]]
    cases.append(("route-plan", value, "single-point road geometry"))
    value = deepcopy(examples["route-plan"]); value["input_data"]["solver_time_limit_seconds"] = 61
    cases.append(("route-plan", value, "excess saved solver budget"))

    # In-memory structural boundary only: this does not establish real road data
    # or replace the application's finalization, provenance and source checks.
    ready = deepcopy(examples["route-plan"])
    ready.update(status="READY", finalized_at=ready["updated_at"])
    ready["input_data"]["data_kind"] = "ROUTED"
    ready["result"].update(status="READY", data_kind="ROUTED", operational_ready=True)
    require(validators["route-plan"].is_valid(ready), "READY schema has no valid structural boundary example")
    value = deepcopy(ready); value["result"]["data_kind"] = "SYNTHETIC"
    cases.append(("route-plan", value, "synthetic result on otherwise valid READY boundary"))
    value = deepcopy(ready); value["result"].update(validation="FAIL", blocking_issues=["WINDOW: service starts after its hard window"])
    cases.append(("route-plan", value, "failed result marked ready"))
    value = deepcopy(ready); value["result"]["operational_ready"] = False
    cases.append(("route-plan", value, "ready result not operationally ready"))
    value = deepcopy(ready); value["result"]["routes"] = []
    cases.append(("route-plan", value, "empty plan marked ready"))
    for name, value, description in cases:
        require(not validators[name].is_valid(value), f"Schema failed to reject {description}")
    return len(cases)


def main() -> None:
    schemas = {path.stem.removesuffix(".schema"): read_json(path) for path in (ROOT / "schemas").glob("*.json")}
    resources = []
    schema_ids = {}
    documents = {}
    for name, data in schemas.items():
        Draft202012Validator.check_schema(data)
        path = (ROOT / "schemas" / f"{name}.schema.json").resolve()
        resource = Resource.from_contents(data)
        resources.extend([(data["$id"], resource), (path.as_uri(), resource)])
        schema_ids[data["$id"]] = path
        documents[path] = data
    registry = Registry().with_resources(resources)
    validators = {name: Draft202012Validator(data, registry=registry, format_checker=FormatChecker()) for name, data in schemas.items()}
    examples = {name: read_json(ROOT / "examples" / f"{name}.json") for name in EXAMPLES}
    require({path.stem for path in (ROOT / "examples").glob("*.json")} == set(EXAMPLES), "Unregistered JSON example")
    for name, schema_name in EXAMPLES.items():
        validators[schema_name].validate(examples[name])
    # Generated from the application; export_contracts.py --check detects drift.
    api_path = ROOT / "backend" / "openapi.json"
    api = read_json(api_path)
    documents[api_path] = api
    require(api["openapi"] == "3.1.0", "OpenAPI version mismatch")
    operations = []
    for path, methods in api["paths"].items():
        for method, operation in methods.items():
            if method not in {"get", "post", "put", "patch", "delete", "options", "head", "trace"}:
                continue
            operations.append(operation["operationId"])
            require(bool(operation["responses"]), f"Missing responses: {method} {path}")
            parameters = [pointer(api, p["$ref"].split("#", 1)[1]) if "$ref" in p else p for p in operation.get("parameters", [])]
            declared = {p["name"] for p in parameters if p["in"] == "path" and p.get("required")}
            require(declared == set(re.findall(r"\{([^}]+)\}", path)), f"Path parameter mismatch: {path}")
    require(len(operations) == len(set(operations)), "Duplicate operationId")
    references = check_references(documents, schema_ids)
    links = check_links()
    csv_rows = check_csv(validators, examples)
    negatives = negative_checks(validators, examples)
    compose = yaml.safe_load((ROOT / "deploy/docker-compose.example.yml").read_text())
    require(set(compose["services"]) == {"postgres", "setup", "api", "worker"}, "Unexpected application service")
    require(compose['services']['worker']['image'] == compose['services']['api']['image'], 'Worker must share application image')
    require(compose['services']['worker']['command'] == ['python','-m','app.worker'], 'Worker command differs')
    require('ports' not in compose['services']['worker'], 'Worker must not expose ports')
    require(compose['services']['worker']['depends_on']['setup']['condition'] == 'service_completed_successfully', 'Worker must wait for setup')
    require(compose["services"]["api"]["image"] == compose["services"]["setup"]["image"], "API and setup must share the image")
    require(compose["services"]["api"]["depends_on"]["setup"]["condition"] == "service_completed_successfully", "API must wait for migrations/provisioning")
    require(compose['services']['setup']['command'] == ['python','-m','app.setup_database'], 'Setup must separate role bootstrap from schema migration')
    for name in ['api','worker']:
        environment = compose['services'][name]['environment']
        require(environment['DB_USER'] == '${RUNTIME_DB_USER:-vrp_app}', 'Runtime must use restricted role')
        require(environment['DB_PASSWORD'] == '${RUNTIME_DB_PASSWORD:?Set RUNTIME_DB_PASSWORD in .env}', 'Runtime credential differs')
        require(not ({'POSTGRES_PASSWORD','MIGRATION_DB_PASSWORD'} & set(environment)), 'Runtime must not receive privileged credentials')
    for service in compose['services'].values():
        require(service['logging'] == {'driver':'local','options':{'max-size':'10m','max-file':'3'}}, 'Compose logs must be bounded and rotated')
    for name in ["postgres", "api"]:
        require(all(port.startswith("127.0.0.1:") for port in compose["services"][name]["ports"]), "Demo ports must stay loopback-only")
    env_names = {line.split("=", 1)[0] for line in (ROOT / "deploy/env.example").read_text().splitlines() if line and not line.startswith("#") and "=" in line}
    referenced_env = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", (ROOT / "deploy/docker-compose.example.yml").read_text()))
    require(referenced_env <= env_names, f"Undocumented Compose variables: {referenced_env - env_names}")
    env_values = dict(line.split("=", 1) for line in (ROOT / "deploy/env.example").read_text().splitlines() if line and not line.startswith("#") and "=" in line)
    map_style = "https://tiles.openfreemap.org/styles/liberty"
    require(env_values.get("MAP_STYLE_URL") == map_style, "Default map style differs from accepted baseline")
    require(compose["services"]["api"]["environment"]["MAP_STYLE_URL"] == "${MAP_STYLE_URL:-" + map_style + "}", "Compose map-style default differs from env.example")
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    require("Apache License" in license_text and "Version 2.0, January 2004" in license_text, "Selected license text missing")
    require(not list(ROOT.glob("LICENSE-PROPOSED*")), "Superseded proposed-license file remains")
    print(f"PASS: {len(schemas)} generated JSON schemas are structurally valid.")
    print(f"PASS: {len(examples)} JSON examples validate against them with format checks.")
    print(f"PASS: {csv_rows} CSV rows match the documented JSON inputs (import review reset retained).")
    print(f"PASS: {references} local schema/OpenAPI references and {links} Markdown file links resolve.")
    print(f"PASS: {len(api['paths'])} API paths / {len(operations)} unique operations in backend/openapi.json have responses and consistent path parameters.")
    print(f"PASS: {negatives} negative schema cases are rejected.")
    print("PASS: Compose YAML has loopback-only database/API, same-image worker, setup ordering and declared environment variables.")
    print("PASS: selected Apache-2.0 file and configurable OpenFreeMap default are present and consistent.")
    print("OUTSIDE THIS CHECK: contract drift (scripts/export_contracts.py --check), example model/schedule/cost checks "
          "(backend/tests/test_examples.py), other Docker/application tests (see deploy/README.md), engine integrations, "
          "rendered Mermaid, security or road/legal correctness.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"FAIL: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1) from error
