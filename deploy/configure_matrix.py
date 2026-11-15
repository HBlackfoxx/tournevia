"""Write a new pinned-Valhalla service config without changing an existing graph."""
import argparse
import json
from pathlib import Path


def configure_matrix(config: dict) -> None:
    # Pinned CostMatrix continues searching after finding its connections. Bound
    # that refinement work, retain buffers for the application's 48-point tiles,
    # and recover destination-access connections through the engine's second pass.
    # Truck access, hierarchy limits, graph data and road routing stay unchanged.
    config['thor']['costmatrix'].update(allow_second_pass=True,
                                       max_reserved_locations=48, max_iterations=1280)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    config = json.loads(args.input.read_text())
    configure_matrix(config)
    # Refuse overwriting either the original configuration or another candidate.
    with args.output.open('x') as output:
        output.write(json.dumps(config, indent=2) + '\n')


if __name__ == '__main__':
    main()
