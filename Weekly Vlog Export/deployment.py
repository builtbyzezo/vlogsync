"""Bind the portable controller to the user's mounted Drive workspace."""
import ctypes
import json
import os
from pathlib import Path


MANAGER = 'VlogSync'
MARKER = Path('Configuration/controller.json')
YOUTUBE = ('My Drive', '3 - Youtube')
CAPCUT_BUILD = '9.8.0.4213'


def locate():
    override = os.environ.get('WEEKLY_VLOG_MANAGER_ROOT')
    if override:
        locations = [Path(override)]
    else:
        drives = ctypes.windll.kernel32.GetLogicalDrives()
        locations = [Path(chr(65+i)+':/').joinpath(*YOUTUBE,MANAGER)
                     for i in range(26) if drives & (1<<i)]
    valid = []
    for folder in locations:
        marker = folder/MARKER
        if not marker.is_file():
            continue
        config = json.loads(marker.read_bytes())
        if str(config.get('workspace','')).startswith('weekly-vlog-export-'):
            valid.append((folder,config))
    if len(valid)!=1:
        raise RuntimeError('Expected one VlogSync workspace. Check the mount or the local workspace override.')
    return valid[0]


def under(root, value):
    relative = Path(value)
    if not relative.parts or relative.anchor or '..' in relative.parts:
        raise RuntimeError(f'Expected a safe workspace-relative path: {value}')
    return root/relative


def publication(path, youtube):
    """Interpret legacy absolute Drive paths relative to the verified mount."""
    path = Path(path)
    if youtube is not None and path.is_absolute() and path.parts[1:3]==YOUTUBE:
        return youtube.joinpath(*path.parts[3:])
    return path


def historical_run(manager):
    config=json.loads((manager/MARKER).read_bytes())
    run=config.get('historical_run', '')
    if not run or Path(run).name!=run:
        raise RuntimeError('Set historical_run to the validated historical audit run in local configuration')
    return run
