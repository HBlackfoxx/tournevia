"""Create a committed-source release kit, verify/build an extracted kit, print a version's
changelog section, or sign release files.

No customer data, credentials or routing graph is included; images are published by
.github/workflows/release.yml, never by this script.
"""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
from urllib.parse import quote
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LOCAL_NAMES = {'AGENTS.md', 'CODEX_START_PROMPT.md', 'PLAN.md', 'REVIEW.md',
               'DOCUMENTATION_STATUS.md', 'VALIDATION_REPORT.md', '.codex', '.agents',
               '.git', '.venv', 'node_modules', '__pycache__', '.pytest_cache',
               '.pytest-tmp', 'backups', 'private-setup.url', 'workspace.env', 'test-login.json'}


# Image SBOM generator (Debian and Python packages); tag and digest verified when pinned.
SYFT = 'anchore/syft:v1.54.1@sha256:3eb5379ba7b409c3f4069b686110527af0c47df993fa5c10d13e7cf34f49b1aa'
SIGNATURE_NAMESPACE = 'tournevia-release'
HASHES = {'sha512': 'SHA-512', 'sha384': 'SHA-384', 'sha256': 'SHA-256'}


def allowed(name: str) -> bool:
    path = PurePosixPath(name)
    return (not path.is_absolute() and '..' not in path.parts and '\\' not in name
            and ':' not in name and not any(part in LOCAL_NAMES for part in path.parts)
            and not path.name.startswith('.env') and not name.startswith(('deploy/runtime/', 'web/dist/'))
            and path.suffix.lower() not in {'.pem', '.key', '.pbf', '.dump', '.log'})


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(['git', '-C', str(root), *args])


def pack(root: Path, version: str, output: Path) -> Path:
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.]+)?', version):
        raise ValueError('Use a version such as 0.8.0-rc.1.')
    if git(root, 'status', '--porcelain', '--untracked-files=all').strip():
        raise ValueError('Commit application changes first; release kits use a clean Git HEAD.')
    revision = git(root, 'rev-parse', 'HEAD').decode().strip()
    name = f'tournevia-{version}-{revision[:12]}'
    archive = output / (name + '.zip')
    checksum = output / (name + '.zip.sha256')
    if archive.exists() or checksum.exists():
        raise ValueError('Release output already exists; never overwrite an existing candidate.')
    source = zipfile.ZipFile(io.BytesIO(git(root, 'archive', '--format=zip', 'HEAD')))
    files = {}
    for item in source.infolist():
        if item.is_dir():
            continue
        if not allowed(item.filename) or item.filename == 'release.json' or stat.S_ISLNK(item.external_attr >> 16):
            raise ValueError(f'Local/private or unsupported tracked path: {item.filename}')
        files[item.filename] = source.read(item)
    manifest = {'version': version, 'revision': revision, 'status': 'local-release-candidate',
                'platform_tested': 'linux/amd64', 'files': {key: digest(value) for key, value in sorted(files.items())}}
    output.mkdir(parents=True, exist_ok=True)
    # Git's committed timestamps plus sorted entries repeat the same bytes on one machine; another
    # time zone or compression library changes the bytes, not the content.
    stamp = source.infolist()[0].date_time
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as result:
        for filename, content in sorted({**files, 'release.json': (json.dumps(manifest, indent=2)+'\n').encode()}.items()):
            item = zipfile.ZipInfo(name + '/' + filename, date_time=stamp)
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = (stat.S_IFREG | 0o644) << 16
            result.writestr(item, content)
    checksum.write_text(digest(archive.read_bytes()) + '  ' + archive.name + '\n', encoding='ascii')
    return archive


def notes(root: Path, version: str) -> str:
    """The changelog section of one version, without its heading."""
    text = (root / 'CHANGELOG.md').read_text(encoding='utf-8')
    section = re.search(rf'^## {re.escape(version)} [^\n]*\n(.*?)(?=^## |\Z)', text, re.DOTALL | re.MULTILINE)
    if not section:
        raise ValueError(f'CHANGELOG.md has no section for version {version}.')
    return section.group(1).strip() + '\n'


def verify(root: Path) -> dict:
    manifest = json.loads((root / 'release.json').read_text(encoding='utf-8'))
    if not re.fullmatch(r'[0-9a-f]{40}', manifest['revision']):
        raise ValueError('Invalid release revision.')
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.]+)?', manifest['version']):
        raise ValueError('Invalid release version.')
    if not manifest['files']:
        raise ValueError('Empty release manifest.')
    for name, expected in manifest['files'].items():
        if not allowed(name):
            raise ValueError('Unsafe release path.')
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or digest(path.read_bytes()) != expected:
            raise ValueError(f'Release file changed: {name}')
    return manifest


def web_sbom(root: Path, version: str) -> dict:
    """CycloneDX list of the frontend's production npm packages, selected like the bundled notices."""
    lock = json.loads((root / 'web/package-lock.json').read_text(encoding='utf-8'))
    components = {}
    for location, entry in sorted(lock['packages'].items()):
        if not location or entry.get('dev'):
            continue
        name = entry.get('name') or location.rsplit('node_modules/', 1)[1]
        purl = f'pkg:npm/{quote(name, safe="/")}@{entry["version"]}'
        component = {'type': 'library', 'bom-ref': purl, 'name': name, 'version': entry['version'], 'purl': purl}
        if entry.get('license'):
            component['licenses'] = [{'expression': entry['license']}]
        algorithm, _, value = entry.get('integrity', '').partition('-')
        if algorithm in HASHES:
            component['hashes'] = [{'alg': HASHES[algorithm], 'content': base64.b64decode(value).hex()}]
        components[purl] = component
    # No timestamp or serial number: the same revision produces the same document.
    return {'bomFormat': 'CycloneDX', 'specVersion': '1.5', 'version': 1,
            'metadata': {'component': {'type': 'application', 'name': 'tournevia-web', 'version': version}},
            'components': list(components.values())}


def image_sbom(image: str) -> bytes:
    """CycloneDX inventory of an image's OS and Python packages, read from a saved archive."""
    with tempfile.TemporaryDirectory(dir=ROOT / 'deploy/runtime') as folder:
        subprocess.run(['docker', 'save', '-o', str(Path(folder) / 'image.tar'), image], check=True)
        # The scanner sees only this archive, never the Docker daemon.
        return subprocess.check_output(['docker', 'run', '--rm', '--network', 'none', '-e', 'SYFT_FILE_METADATA_SELECTION=none',
                                        '-v', f'{folder}:/in:ro', SYFT, 'docker-archive:/in/image.tar',
                                        '-o', 'cyclonedx-json', '--quiet'])


def sign(key: Path, files: list[Path]) -> list[Path]:
    """Write FILE.sig with an operator-held SSH key; verify with ssh-keygen -Y verify."""
    signatures = []
    for file in files:
        signature = file.with_name(file.name + '.sig')
        if not file.is_file() or signature.exists():
            raise ValueError(f'Sign an existing file without a signature: {file.name}')
        subprocess.run(['ssh-keygen', '-Y', 'sign', '-f', str(key), '-n', SIGNATURE_NAMESPACE, str(file)],
                       check=True, stdout=subprocess.DEVNULL)
        signatures.append(signature)
    return signatures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    package = commands.add_parser('pack')
    package.add_argument('--version', required=True)
    package.add_argument('--output', type=Path, default=ROOT/'deploy/runtime/releases')
    describing = commands.add_parser('notes', help="Print a version's changelog section.")
    describing.add_argument('--version', required=True)
    commands.add_parser('verify')
    commands.add_parser('build')
    signing = commands.add_parser('sign', help='Sign release files with an SSH key (writes FILE.sig).')
    signing.add_argument('--key', type=Path, required=True, help='Private SSH key held by the release signer.')
    signing.add_argument('files', type=Path, nargs='+')
    args = parser.parse_args()
    if args.action == 'pack':
        print(pack(ROOT, args.version, args.output))
        return
    if args.action == 'notes':
        sys.stdout.buffer.write(notes(ROOT, args.version).encode())
        return
    if args.action == 'sign':
        for signature in sign(args.key, args.files):
            print('Signed: ' + str(signature))
        return
    manifest = verify(ROOT)
    print(f'Verified {len(manifest["files"])} source files at {manifest["revision"]}.', flush=True)
    if args.action == 'build':
        image = f'tournevia-app:{manifest["version"]}-{manifest["revision"][:12]}'
        subprocess.run(['docker', 'build', '--platform', 'linux/amd64', '-f', 'deploy/Dockerfile.vroom',
                        '-t', 'tournevia-vroom:1.14.0', '.'], cwd=ROOT, check=True)
        subprocess.run(['docker', 'build', '--platform', 'linux/amd64', '--target', 'runtime',
                        '--build-arg', 'VCS_REF='+manifest['revision'], '-t', image, '.'], cwd=ROOT, check=True)
        identity = json.loads(subprocess.check_output(['docker', 'image', 'inspect', image]))[0]
        if identity['Config']['Labels'].get('org.opencontainers.image.revision') != manifest['revision']:
            raise ValueError('Built image revision differs from the release manifest.')
        record = ROOT/'deploy/runtime/release-image.json'
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(json.dumps({'image':image, 'id':identity['Id'], 'revision':manifest['revision'],
                                      'platform':identity['Os']+'/'+identity['Architecture']}, indent=2)+'\n', encoding='utf-8')
        sboms = ROOT/'deploy/runtime/sbom'
        sboms.mkdir(parents=True, exist_ok=True)
        tag = image.split(':', 1)[1]
        (sboms/f'{tag}.image.cdx.json').write_bytes(image_sbom(image))
        (sboms/f'{tag}.web.cdx.json').write_text(json.dumps(web_sbom(ROOT, tag), indent=2)+'\n', encoding='utf-8')
        print('Built local image: '+image)
        print('Image identity: '+identity['Id'])
        print('Software bills of materials (CycloneDX): '+str(sboms/f'{tag}.image.cdx.json')+' and .web.cdx.json')
        print('Start a NEW workspace: python scripts/start_local.py --image '+image)
        print('Existing workspace image selection is preserved; see deploy/RELEASE.md for upgrades.')


if __name__ == '__main__':
    main()
