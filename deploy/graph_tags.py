"""Valhalla's tag file, changed so that roads limited by `maxweightrating` keep their weight limit.

Valhalla reads a road's gross-weight limit from OpenStreetMap's `maxweight` tag only. France's
roads carry that limit as `maxweightrating`: an automated edit added the tag in February 2026 and
removed `maxweight` in November 2026, so a graph built with Valhalla's own tag file from a later
extract has none of these limits. valhalla-graph.lua is Valhalla's file with three lines changed.
"""
from pathlib import Path

FILE = Path(__file__).with_name('valhalla-graph.lua')
# Our line: Valhalla's line.
CHANGED = {
    'kv["maxweight"] = normalize_weight(kv["maxweight"] or kv["maxweightrating"])':
        'kv["maxweight"] = normalize_weight(kv["maxweight"])',
    'kv["maxweight_forward"] = normalize_weight(kv["maxweight:forward"] or kv["maxweightrating:forward"])':
        'kv["maxweight_forward"] = normalize_weight(kv["maxweight:forward"])',
    'kv["maxweight_backward"] = normalize_weight(kv["maxweight:backward"] or kv["maxweightrating:backward"])':
        'kv["maxweight_backward"] = normalize_weight(kv["maxweight:backward"])',
}


def shipped(text: str) -> str:
    """Our tag file as Valhalla ships it: without our header, with Valhalla's three lines."""
    text = ''.join(line for line in text.splitlines(keepends=True) if not line.startswith('--!'))
    for ours, theirs in CHANGED.items():
        if text.count(ours) != 1:
            raise ValueError(f'valhalla-graph.lua must contain this line once: {ours}')
        text = text.replace(ours, theirs)
    return text


def tag_file(builder: Path) -> Path:
    """The tag file to build with, once it matches the tag file inside this Valhalla's graph builder."""
    if shipped(FILE.read_text(encoding='utf-8')).encode() not in builder.read_bytes():
        raise SystemExit('deploy/valhalla-graph.lua was not made from this Valhalla version. Make it again from '
                         "this version's lua/graph.lua, as deploy/README.md describes, before building a graph.")
    return FILE
