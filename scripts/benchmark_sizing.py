"""Measure the sizing scenarios under emulated server sizes and write a report.

A server size is emulated with Docker: the router and the calculation run pinned to the
same CPU cores (`--cpuset-cpus`), so they share the CPUs as on one machine, and each has
a memory limit. The router's memory is sampled every second while each scenario runs.
Prepare the graphs first (deploy/README.md), build the engine-test image, then run for
example:

    python scripts/benchmark_sizing.py --image tournevia-engine-test:local --cores 24-31

Results are written to backend/benchmarks/results/ as JSON and Markdown. Times are from
this one machine and are not a guarantee for other hardware.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import threading
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
ENGINES = json.loads((ROOT / 'deploy/engines.json').read_text())
# vCPUs, router memory, router threads, calculation memory, matrix requests in flight.
PROFILES = {
    '2 vCPU / 8 GB': (2, '4g', 2, '2g', 1),
    '4 vCPU / 16 GB': (4, '8g', 4, '2g', 2),
    '8 vCPU / 32 GB': (8, '12g', 4, '2g', 4),
}
GRAPHS = {
    'Île-de-France': ('bind', ROOT / 'deploy/runtime', '/data/regional-preview-20260927/valhalla.json'),
    'France': ('volume', 'vrp-france-graph', '/data/france/valhalla.json'),
}
SCENARIOS = [('starter', 'Île-de-France'), ('small-fleet', 'Île-de-France'), ('medium-fleet', 'Île-de-France'),
             ('large-paris', 'Île-de-France'), ('regional', 'Île-de-France'), ('regional', 'France')]
NETWORK = 'vrp-sizing'


def docker(*args, check=True) -> str:
    return subprocess.run(['docker', *args], check=check, capture_output=True, text=True).stdout


def memory_mib(container: str) -> float:
    text = docker('stats', container, '--no-stream', '--format', '{{.MemUsage}}', check=False).split('/')[0].strip()
    number, unit = float(''.join(c for c in text if c.isdigit() or c == '.') or 0), text.lstrip('0123456789.')
    return number * {'GiB': 1024, 'MiB': 1, 'KiB': 1 / 1024, 'B': 1 / 1048576}.get(unit, 1)


class Sampler(threading.Thread):
    def __init__(self, container: str):
        super().__init__(daemon=True)
        self.container, self.peak, self.running = container, 0.0, True

    def run(self):
        while self.running:
            self.peak = max(self.peak, memory_mib(self.container))
            time.sleep(1)


def start_router(graph: str, cpus: str, memory: str, threads: int, port: int) -> str:
    kind, source, config = GRAPHS[graph]
    name = 'vrp-sizing-router'
    docker('rm', '-f', name, check=False)
    mount = (['-v', f'{source}:/data:ro'] if kind == 'volume'
             else ['--mount', f'type=bind,source={source},target=/data,readonly'])
    docker('run', '-d', '--name', name, '--network', NETWORK, '--cpuset-cpus', cpus, '--memory', memory,
           '-p', f'127.0.0.1:{port}:8002', *mount, '--entrypoint', 'valhalla_service', ENGINES['valhalla_image'],
           config, str(threads))
    for _ in range(120):
        try:
            with urlopen(f'http://127.0.0.1:{port}/status', timeout=2):
                return name
        except OSError:
            time.sleep(1)
    raise SystemExit(f'Router for {graph} did not start.')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True, help='Engine-test image with native VROOM and the benchmarks.')
    parser.add_argument('--cores', default='24-31', help='Host cores to use, at least as many as the largest profile.')
    parser.add_argument('--repeats', type=int, default=2)
    parser.add_argument('--profiles', nargs='*', default=list(PROFILES))
    args = parser.parse_args()
    first, last = map(int, args.cores.split('-'))
    docker('network', 'create', NETWORK, check=False)
    rows, started = [], datetime.now(timezone.utc)
    try:
        for profile in args.profiles:
            vcpus, router_memory, threads, calc_memory, concurrency = PROFILES[profile]
            if last - first + 1 < vcpus:
                raise SystemExit(f'{profile} needs {vcpus} cores in --cores.')
            cpus = f'{first}-{first + vcpus - 1}'
            for graph in dict.fromkeys(g for _, g in SCENARIOS):
                router = start_router(graph, cpus, router_memory, threads, 18090)
                for name, scenario_graph in SCENARIOS:
                    if scenario_graph != graph:
                        continue
                    sampler = Sampler(router); sampler.start()
                    print(f'{profile} · {graph} · {name}', flush=True)
                    output = docker('run', '--rm', '--network', NETWORK, '--cpuset-cpus', cpus, '--memory', calc_memory,
                                    args.image, 'python', '-m', 'benchmarks.sizing', '--scenario', name,
                                    '--valhalla-url', f'http://{router}:8002', '--matrix-concurrency', str(concurrency),
                                    '--repeats', str(args.repeats), check=False)
                    sampler.running = False; sampler.join()
                    for line in output.splitlines():
                        if line.startswith('{'):
                            rows.append({**json.loads(line), 'profile': profile, 'graph': graph,
                                         'router_threads': threads, 'router_memory_limit': router_memory,
                                         'router_peak_mib': round(sampler.peak)})
                            print('  ', rows[-1]['status'], rows[-1]['seconds'], 's', flush=True)
                docker('rm', '-f', router, check=False)
    finally:
        docker('rm', '-f', 'vrp-sizing-router', check=False)
        docker('network', 'rm', NETWORK, check=False)
    out = ROOT / 'backend/benchmarks/results'
    out.mkdir(parents=True, exist_ok=True)
    stamp = started.strftime('%Y-%m-%d')
    cpu = docker('run', '--rm', args.image, 'sh', '-c', "grep -m1 'model name' /proc/cpuinfo | cut -d: -f2").strip()
    report = {'recorded_utc': started.isoformat(), 'cpu_model': cpu, 'data_kind': 'SYNTHETIC',
              'method': 'Router and calculation pinned to the same cores with Docker memory limits; whole calculate() '
                        'under the 180 s job deadline, 5 s solver budget; router memory sampled each second.',
              'rows': rows}
    (out / f'sizing-{stamp}.json').write_text(json.dumps(report, indent=1, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')
    lines = [f'# Sizing measurements, {stamp}', '', f'CPU: {cpu}. {report["method"]}', '',
             '| Profile | Graph | Scenario | Vehicles / deliveries | Run | Result | Seconds | Router peak MiB |',
             '|---|---|---|---|---|---|---|---|']
    for r in rows:
        lines.append(f"| {r['profile']} | {r['graph']} | {r['scenario']} | {r['vehicles']} / {r['deliveries']} | "
                     f"{r['repeat']} | {r['status']} | {r['seconds']} | {r['router_peak_mib']} |")
    (out / f'sizing-{stamp}.md').write_text('\n'.join(lines) + '\n', encoding='utf-8', newline='\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
