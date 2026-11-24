"""Resolve exports whose Drive parents changed under a verified relocation."""
import json
import os
from pathlib import Path

STATE = Path(os.environ['LOCALAPPDATA']) / 'VlogSync'
ROLE_DIR = {'Master': '1080p Master Vlogs', 'Proxy': '720p Editor Proxies'}


def require_ready():
    if (STATE / 'drive-organization-in-progress').exists():
        raise RuntimeError('Drive organization is in progress. Preview and export are disabled.')


def mounted_path(youtube, legacy, role, checksum, old_stamp):
    record = STATE / 'drive-organization-v1.json'
    if not record.is_file():
        if not legacy.is_file():
            raise RuntimeError('Restore the verified Drive organization binding before export.')
        return legacy, old_stamp
    state = json.loads(record.read_bytes())
    if state['schema'] != 1:
        raise RuntimeError('Unknown Drive organization binding')
    key = legacy.relative_to(youtube).as_posix()
    row = state['files'][key]
    relative = Path(ROLE_DIR[role], legacy.name)
    if row['path'] != relative.as_posix() or row['sha256'] != checksum or row['stamp'][0] != old_stamp[0]:
        raise RuntimeError(f'Drive organization identity mismatch: {key}')
    return youtube / relative, row['stamp']
