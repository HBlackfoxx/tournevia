"""Release boundary: no local secrets, immutable source, detectable modification."""
import importlib.util
import json
from pathlib import Path
import subprocess
import zipfile

import pytest

spec = importlib.util.spec_from_file_location('release', Path(__file__).resolve().parents[2]/'scripts/release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.mark.parametrize('name', ['.env', 'PLAN.md', '.codex/settings.json', 'deploy/runtime/workspace.env',
                                  'backups/company.dump', '../outside', '/absolute', 'C:/secret', 'link\\secret', 'private.key'])
def test_reject_local_paths(name):
    assert not release.allowed(name)


def repository(path):
    path.mkdir()
    subprocess.run(['git', 'init', '-q', str(path)], check=True)
    (path/'app.py').write_text('print("source")\n')
    (path/'.gitignore').write_text('.env\ndeploy/runtime/\n')
    subprocess.run(['git','-C',str(path),'add','.'],check=True)
    subprocess.run(['git','-C',str(path),'-c','user.name=Release test','-c','user.email=test@example.invalid',
                    'commit','-qm','Fixture'],check=True)
    return path


def test_pack_ignores_private_files_is_repeatable_and_verifies_content(tmp_path):
    root = repository(tmp_path/'source')
    (root/'.env').write_text('PRIVATE=never include')
    first = release.pack(root, '0.8.0-rc.1', tmp_path/'first')
    second = release.pack(root, '0.8.0-rc.1', tmp_path/'second')
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert not any(name.endswith('.env') for name in archive.namelist())
        archive.extractall(tmp_path/'unpacked')
    extracted = next((tmp_path/'unpacked').iterdir())
    assert release.verify(extracted)['revision'] == release.git(root,'rev-parse','HEAD').decode().strip()
    (extracted/'app.py').write_text('changed')
    with pytest.raises(ValueError, match='changed'):
        release.verify(extracted)
    with pytest.raises(ValueError, match='overwrite'):
        release.pack(root, '0.8.0-rc.1', tmp_path/'first')


def test_pack_refuses_uncommitted_and_tracked_private_files(tmp_path):
    root = repository(tmp_path/'source')
    (root/'PLAN.md').write_text('Private handoff')
    with pytest.raises(ValueError, match='Commit'):
        release.pack(root,'0.8.0',tmp_path/'out')
    subprocess.run(['git','-C',str(root),'add','PLAN.md'],check=True)
    subprocess.run(['git','-C',str(root),'-c','user.name=Release test','-c','user.email=test@example.invalid',
                    'commit','-qm','Private file'],check=True)
    with pytest.raises(ValueError, match='private'):
        release.pack(root,'0.8.0',tmp_path/'out')


def test_verify_rejects_manifest_path_escape(tmp_path):
    (tmp_path/'release.json').write_text(json.dumps({'revision':'a'*40,'version':'0.8.0','files':{'../secret':'x'}}))
    with pytest.raises(ValueError, match='Unsafe'):
        release.verify(tmp_path)


def test_web_sbom_lists_exactly_the_production_packages_with_their_lockfile_integrity():
    root = Path(release.__file__).resolve().parents[1]
    lock = json.loads((root/'web/package-lock.json').read_text(encoding='utf-8'))['packages']
    sbom = release.web_sbom(root, '0.8.0-test')
    names = {c['name'] for c in sbom['components']}
    production = {entry.get('name') or location.rsplit('node_modules/', 1)[1]
                  for location, entry in lock.items() if location and not entry.get('dev')}
    assert names == production and {'react', 'react-dom', 'scheduler', 'maplibre-gl'} <= names
    assert not names & {'vite', 'vitest', 'typescript', 'eslint'}
    refs = [c['bom-ref'] for c in sbom['components']]
    assert len(refs) == len(set(refs)) and 'pkg:npm/%40mapbox/point-geometry@' in ' '.join(refs)
    assert all(len(c['hashes'][0]['content']) == 128 for c in sbom['components'])
    assert release.web_sbom(root, '0.8.0-test') == sbom


@pytest.mark.skipif(not __import__('shutil').which('ssh-keygen'), reason='OpenSSH ssh-keygen is not installed.')
def test_signature_verifies_only_the_signed_bytes_for_the_named_signer(tmp_path):
    key = tmp_path/'release_key'
    subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'test', '-f', str(key)], check=True)
    kit = tmp_path/'kit.zip'
    kit.write_bytes(b'release candidate bytes')
    [signature] = release.sign(key, [kit])
    public = ' '.join((tmp_path/'release_key.pub').read_text().split()[:2])
    allowed = tmp_path/'allowed_signers'
    allowed.write_text(f'release@example.test namespaces="{release.SIGNATURE_NAMESPACE}" {public}\n')

    def verified(identity='release@example.test'):
        with kit.open('rb') as data:
            return subprocess.run(['ssh-keygen', '-Y', 'verify', '-f', str(allowed), '-I', identity,
                                   '-n', release.SIGNATURE_NAMESPACE, '-s', str(signature)],
                                  stdin=data, capture_output=True).returncode == 0
    assert verified()
    assert not verified('someone-else@example.test')
    kit.write_bytes(b'release candidate bytes, changed')
    assert not verified()
    with pytest.raises(ValueError):
        release.sign(key, [kit])  # An existing signature is never overwritten.


def test_notes_are_one_version_section_without_its_heading(tmp_path):
    (tmp_path/'CHANGELOG.md').write_text('# Changelog\n\n## Unreleased\n\nNothing yet.\n\n## 0.8.0 — 15 November 2026\n\n'
                                         'First.\n\n### Names\n\n- One\n\n## 0.7.0 — earlier\n\nOlder.\n', encoding='utf-8')
    assert release.notes(tmp_path, '0.8.0') == 'First.\n\n### Names\n\n- One\n'
    assert release.notes(tmp_path, '0.7.0') == 'Older.\n'
    with pytest.raises(ValueError):
        release.notes(tmp_path, '0.8')
