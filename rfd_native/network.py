"""Local network plan storage. Loading a plan never writes to a radio."""
import json
from pathlib import Path

SHARED_FIELDS = ('NETID', 'AIR_SPEED', 'MIN_FREQ', 'MAX_FREQ', 'NUM_CHANNELS')


def validate_plan(data, *, draft=False):
    if not isinstance(data, dict) or data.get('profile') not in ('Async mesh', 'SiK pair'):
        raise ValueError('Invalid network profile')
    radios = data.get('radios', [])
    if not isinstance(radios, list) or not 2 <= len(radios) <= 32767:
        raise ValueError('A plan must have 2–32767 radios')
    if data['profile'] == 'SiK pair' and len(radios) != 2:
        raise ValueError('SiK pair plans require exactly two radios')
    if any(not isinstance(node,dict) for node in radios):
        raise ValueError('Invalid radio entry')
    ids = [int(n['node']) for n in radios]
    if len(set(ids)) != len(ids) or any(not 1 <= n <= 32767 for n in ids):
        raise ValueError('Node IDs must be unique and between 1 and 32767')
    for node in radios:
        if not 1 <= int(node['dest']) <= 32768 or int(node['dest']) == int(node['node']):
            raise ValueError('Invalid destination ID')
        if not isinstance(node['name'], str):
            raise ValueError('Radio name must be text')
        if 'applied' in node and not isinstance(node['applied'],dict):
            raise ValueError('Invalid radio readback record')
    shared = data.get('shared', {})
    for key in SHARED_FIELDS:
        value = shared[key]
        if not isinstance(value, (str,int)):
            raise ValueError(f'Invalid {key}')
        if not draft:
            int(value)
    return data


def desired_settings(profile, shared, node):
    values = {key:str(int(shared[key])) for key in SHARED_FIELDS}
    if int(values['MIN_FREQ']) >= int(values['MAX_FREQ']):
        raise ValueError('Minimum frequency must be below maximum frequency')
    if int(values['NUM_CHANNELS']) < 1 or int(values['AIR_SPEED']) < 1:
        raise ValueError('Air speed and channel count must be positive')
    if profile == 'Async mesh':
        values.update(NODEID=str(int(node['node'])),DESTID=str(int(node['dest'])))
    return values


def provisioning_status(profile, shared, node):
    try:
        wanted = desired_settings(profile,shared,node)
    except (ValueError,KeyError,TypeError):
        return 'Group edits incomplete'
    if not node.get('applied'):
        return 'Needs upload'
    if any(str(node['applied'].get(key)) != value for key,value in wanted.items()):
        return 'Group changed — needs upload'
    return 'Verified ' + node.get('verified_at','previous session')


def save_session(path, data):
    validate_plan(data,draft=True)
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data,indent=2),encoding='utf-8')
    temporary.replace(path)


def load_session(path):
    return validate_plan(json.loads(Path(path).read_text(encoding='utf-8')),draft=True)
