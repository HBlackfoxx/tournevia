"""Collect installed Python licenses and exact package inventory inside the image."""
from importlib.metadata import distributions
import json
from pathlib import Path
import shutil
import subprocess
import sys

# Native libraries inside the psycopg-binary wheel, and the Debian package whose copyright
# file the Dockerfile keeps for each under psycopg-binary-libraries.
BUNDLED = {'libpq': 'libpq5', 'libssl': 'libssl3', 'libcrypto': 'libssl3', 'libldap': 'libldap-2.5-0',
           'liblber': 'libldap-2.5-0', 'libsasl2': 'libsasl2-2', 'libkrb5': 'libkrb5-3',
           'libgssapi_krb5': 'libkrb5-3', 'libk5crypto': 'libkrb5-3', 'libkrb5support': 'libkrb5-3',
           'libkeyutils': 'libkeyutils1', 'libcom_err': 'libcom-err2', 'libcrypt': 'libcrypt1',
           'libpcre': 'libpcre3', 'libselinux': 'libselinux1'}


def bundled(libraries: Path, notices: Path) -> list[str]:
    """Names of the wheel's native libraries; each must have its kept licence statement."""
    names = sorted(file.name for file in libraries.iterdir()) if libraries.is_dir() else []
    for name in names:
        package = BUNDLED.get(name.split('-')[0].split('.')[0])
        if not package or not (notices / f'{package}.copyright').is_file():
            raise RuntimeError(f'No kept license text for bundled library {name}; review before distributing.')
    return names


def collect(output: Path):
    output.mkdir(parents=True, exist_ok=True)
    inventory = []
    for package in sorted(distributions(), key=lambda p: p.metadata['Name'].lower()):
        name = package.metadata['Name']
        files = [p for p in package.files or [] if 'licenses' in p.parts or
                 p.name.lower().startswith(('license', 'licence', 'copying', 'notice', 'authors'))]
        if not files:
            raise RuntimeError(f'No packaged license text for {name}; review before distributing.')
        saved = []
        for item in files:
            # Distribution records may reference paths outside site-packages.
            # Keep the content, but never let their names escape the output root.
            relative = Path(*[part for part in item.parts if part not in ('.', '..', '/')])
            target = output / 'python' / f'{name}-{package.version}' / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(package.locate_file(item), target)
            saved.append(target.relative_to(output).as_posix())
        entry = {'name': name, 'version': package.version,
                 'license': package.metadata.get('License-Expression') or package.metadata.get('License'),
                 'files': saved}
        if name == 'psycopg-binary':
            entry['bundled_libraries'] = bundled(Path(str(package.locate_file('psycopg_binary.libs'))),
                                                 output / 'psycopg-binary-libraries')
        inventory.append(entry)
    (output / 'python-inventory.json').write_text(json.dumps(inventory, indent=2) + '\n', encoding='utf-8')
    shutil.copyfile(Path(sys.base_prefix) / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'LICENSE.txt', output / 'PYTHON-LICENSE.txt')
    # Debian's copyright files remain in /usr/share/doc in the runtime image.
    (output / 'debian-packages.tsv').write_bytes(subprocess.check_output([
        'dpkg-query', '-W', '-f=${Package}\t${Version}\t${Architecture}\n']))
    print(f'Preserved license files for {len(inventory)} installed Python distributions.')


if __name__ == '__main__':
    collect(Path(sys.argv[1]))
