import json
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from start_server import prepare, validate


def certificates(tmp_path):
    folder=tmp_path/'certs';folder.mkdir()
    for name in ('fullchain.pem','privkey.pem'):(folder/name).write_text('test fixture only')
    return folder


def test_preserves_configuration_and_refuses_silent_upgrade(tmp_path):
    cert=certificates(tmp_path);directory=tmp_path/'instance'
    args=(directory,'tournevia:test','https://planner.example','http://router:8002','Reviewed test extract',cert)
    first=prepare(*args);secret=(directory/'workspace.env').read_bytes()
    assert prepare(*args)==first
    assert (directory/'workspace.env').read_bytes()==secret
    assert b'APP_MODE=shared' in secret
    assert len([line for line in secret.splitlines() if b'PASSWORD=' in line])==3
    assert json.loads((directory/'image.json').read_text())['services']['worker']['platform']=='linux/amd64'
    with pytest.raises(ValueError,match='Existing installation differs'):
        prepare(directory,'tournevia:new',*args[2:])
    assert (directory/'workspace.env').read_bytes()==secret


@pytest.mark.parametrize('origin',['http://planner.example','https://planner.example/path','https://user:secret@planner.example','https://planner.example:8443','https://planner.example?q=1'])
def test_requires_exact_https_origin(tmp_path,origin):
    with pytest.raises(ValueError):validate(origin,'http://router:8002',certificates(tmp_path))


def test_refuses_nonempty_directory_and_interpolation(tmp_path):
    cert=certificates(tmp_path);directory=tmp_path/'existing';directory.mkdir();(directory/'keep').write_text('preserve')
    with pytest.raises(ValueError,match='empty'):
        prepare(directory,'tournevia:test','https://planner.example','http://router:8002','Test',cert)
    with pytest.raises(ValueError,match='interpolation'):
        prepare(tmp_path/'new','tournevia:test','https://planner.example','http://router:8002','${SECRET}',cert)


def test_upgrade_backs_up_stops_services_and_records_previous_image(tmp_path,monkeypatch):
    import types
    import start_local
    cert=certificates(tmp_path);directory=tmp_path/'instance'
    state=prepare(directory,'tournevia:old','https://planner.example','http://router:8002','Reviewed test extract',cert)
    calls=[];backups=[]
    monkeypatch.setattr(start_local,'run',lambda args:calls.append(args) or '')
    class Database:
        def backup(self,path):backups.append(path);return path
    class BackupError(Exception):pass
    monkeypatch.setitem(sys.modules,'database_backup',types.SimpleNamespace(BackupError=BackupError,workspace_database=lambda d:Database()))
    compose=['docker','compose','-p',state['project']]
    with pytest.raises(ValueError,match='already uses'):start_local.upgrade_image(directory,state,compose,'tournevia:old')
    with pytest.raises(ValueError,match='interpolation'):start_local.upgrade_image(directory,state,compose,'tournevia:${X}')
    assert not calls and not backups
    upgraded,backup=start_local.upgrade_image(directory,state,compose,'tournevia:new')
    assert calls==[['docker','image','inspect','tournevia:new'],[*compose,'stop','api','worker']]
    assert backup.parent==directory/'backups' and backup.name.startswith('before-upgrade-')
    assert upgraded['image']=='tournevia:new' and upgraded['previous_image']=='tournevia:old'
    assert json.loads((directory/'workspace.json').read_text())==upgraded
    assert {s['image'] for s in json.loads((directory/'image.json').read_text())['services'].values()}=={'tournevia:new'}
    # A failed backup changes nothing and never stops services.
    def failing(path):raise BackupError('backup failed')
    monkeypatch.setitem(sys.modules,'database_backup',types.SimpleNamespace(BackupError=BackupError,workspace_database=lambda d:types.SimpleNamespace(backup=failing)))
    calls.clear()
    with pytest.raises(ValueError,match='nothing was changed'):start_local.upgrade_image(directory,upgraded,compose,'tournevia:newer')
    assert calls==[['docker','image','inspect','tournevia:newer']]
    assert json.loads((directory/'workspace.json').read_text())['image']=='tournevia:new'


def test_cutover_uses_the_workspace_env_file_and_reports_how_to_switch_back(tmp_path,monkeypatch):
    import types
    import database_backup
    import start_local
    calls=[]
    def cutover(database,compose,env_file,target):
        calls.append((database,compose,env_file,target))
        if target=='vrp_missing':raise database_backup.BackupError('Cutover target does not exist.')
        return 'vrp'
    monkeypatch.setitem(sys.modules,'database_backup',types.SimpleNamespace(BackupError=database_backup.BackupError,
        cutover=cutover,switched=database_backup.switched,workspace_database=lambda d:'database:'+d.name))
    message=start_local.switch_database(tmp_path,['docker','compose'],'vrp_restore_'+'a'*32)
    assert calls==[('database:'+tmp_path.name,['docker','compose'],tmp_path/'workspace.env','vrp_restore_'+'a'*32)]
    assert message.startswith('Now running on vrp_restore_') and message.endswith(f'--directory {tmp_path} --cutover-to vrp')
    with pytest.raises(RuntimeError,match='does not exist'):
        start_local.switch_database(tmp_path,['docker','compose'],'vrp_missing')


def test_behind_an_operator_proxy_with_the_router_inside(tmp_path,monkeypatch):
    import start_server
    runtime=tmp_path/'runtime';(runtime/'region').mkdir(parents=True);(runtime/'region'/'valhalla.json').write_text('{}')
    monkeypatch.setattr(start_server,'RUNTIME',runtime)
    directory=tmp_path/'instance'
    state=prepare(directory,'tournevia:test','https://app.example.test','','Reviewed test extract',None,
                  proxy_network='coolify',routing_config='region/valhalla.json')
    config=dict(line.split('=',1) for line in (directory/'workspace.env').read_text().splitlines())
    assert config['PROXY_NETWORK']=='coolify' and config['PROXY_HOST']=='app.example.test' and config['PROXY_ROUTER']==state['project']
    assert config['ROUTING_CONFIG']=='region/valhalla.json' and config['ROUTING_MEMORY']=='4g'
    assert 'TLS_CERT_DIR' not in config and 'VALHALLA_URL' not in config
    assert state['certificate_dir'] is None and state['routing'] is None
    files=start_server.compose_files(state)[1::2]
    assert [Path(f).name for f in files]==['docker-compose.example.yml','docker-compose.routing.yml','docker-compose.proxied.yml']
    assert prepare(directory,'tournevia:test','https://app.example.test',None,'Reviewed test extract',None,
                   proxy_network='coolify',routing_config='region/valhalla.json')==state
    with pytest.raises(ValueError,match='Existing installation differs'):
        prepare(directory,'tournevia:test','https://app.example.test',None,'Reviewed test extract',certificates(tmp_path),
                routing_config='region/valhalla.json')


@pytest.mark.parametrize('config',['/srv/region/valhalla.json','../region/valhalla.json','region/missing.json','region'])
def test_routing_config_must_be_a_kit_runtime_file(tmp_path,monkeypatch,config):
    import start_server
    (tmp_path/'region').mkdir();(tmp_path/'region'/'valhalla.json').write_text('{}')
    monkeypatch.setattr(start_server,'RUNTIME',tmp_path)
    with pytest.raises(ValueError,match='routing configuration'):
        validate('https://app.example.test',None,None,proxy_network='coolify',routing_config=config)


@pytest.mark.parametrize('network',['','-coolify','cool ify','coolify;rm','x'*129])
def test_proxy_network_name_is_checked(network):
    with pytest.raises(ValueError):
        validate('https://app.example.test','http://router:8002',None,proxy_network=network or None)


def test_installations_from_before_the_proxy_option_still_match(tmp_path):
    import start_server
    cert=certificates(tmp_path);directory=tmp_path/'instance'
    args=(directory,'tournevia:test','https://planner.example','http://router:8002','Reviewed test extract',cert)
    state=prepare(*args)
    legacy={key:value for key,value in state.items() if key not in ('proxy_network','routing_config')}
    (directory/'workspace.json').write_text(json.dumps(legacy))
    assert prepare(*args)==legacy
    assert [Path(f).name for f in start_server.compose_files(legacy)[1::2]]==['docker-compose.example.yml','docker-compose.https.yml','docker-compose.vps.yml']
