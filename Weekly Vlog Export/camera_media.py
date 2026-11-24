"""Standalone camera identity, discovery, media probing and conservative grouping."""
from collections import defaultdict
import ctypes
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
import subprocess
from zoneinfo import ZoneInfo


def read_json(path,default=None):
    try:
        return json.loads(path.read_bytes())
    except FileNotFoundError:
        return default


def sampled_id(path):
    """Keep the documented three-sample identity used by existing review records."""
    size=path.stat().st_size
    digest=hashlib.sha256(str(size).encode())
    with path.open('rb') as stream:
        for offset in sorted({0,max(0,size//2-524288),max(0,size-1048576)}):
            stream.seek(offset)
            digest.update(stream.read(1048576))
    return digest.hexdigest()


def filename_time(path):
    match=re.search(r'(?<!\d)(\d{8})[_-](\d{6})(?!\d)',path.stem)
    if match:
        try:
            return datetime.strptime(''.join(match.groups()),'%Y%m%d%H%M%S')
        except ValueError:
            return None


def discover_videos(folder):
    for path in folder.iterdir():
        if (path.is_file() and not path.is_symlink() and path.suffix.casefold()=='.mp4'
                and not path.name.upper().startswith(('LRV_','THM_'))):
            yield path


def detect_camera_folders():
    volumes=ctypes.windll.kernel32.GetLogicalDrives()
    for i in range(26):
        if not volumes & (1<<i):
            continue
        path=Path(f'{chr(65+i)}:/DCIM/Camera01')
        try:
            if path.is_dir() and any(discover_videos(path)):
                yield path
        except OSError:
            continue


def probe(path,ffprobe):
    result=subprocess.run([str(ffprobe),'-v','error','-show_format','-show_streams',
                           '-show_data_hash','sha256','-of','json',str(path)],
                          check=True,capture_output=True,encoding='utf-8',timeout=60)
    return json.loads(result.stdout)


@dataclass(frozen=True)
class Clip:
    path:Path
    start:datetime
    identity:str


@dataclass(frozen=True)
class Group:
    clips:list[Clip]


def parse_clip(path,ffprobe,cfg):
    doc=probe(path,ffprobe)
    local=filename_time(path)
    if local is None:
        raise RuntimeError(f'Unrecognized camera timestamp: {path.name}')
    offsets=cfg.get('offsets',{})
    volume=ctypes.c_uint32()
    folder=path.parent.resolve()
    available=folder.drive and ctypes.windll.kernel32.GetVolumeInformationW(
        str(folder.anchor),None,0,ctypes.byref(volume),None,None,None,0)
    key=f'volume-{volume.value:08x}/'+folder.relative_to(folder.anchor).as_posix().lower()
    correction=cfg.get('device_offsets',{}).get(key,offsets.get(str(folder),0)) if available else offsets.get(str(folder),0)
    local+=timedelta(seconds=float(correction))
    raw=doc.get('format',{}).get('tags',{}).get('creation_time')
    zone=cfg.get('timezone')
    if raw and zone:
        embedded=datetime.fromisoformat(raw.replace('Z','+00:00'))
        if embedded.tzinfo and abs(embedded.astimezone(ZoneInfo(zone)).replace(tzinfo=None)-local)>timedelta(minutes=2):
            raise RuntimeError(f'Camera filename and embedded time disagree: {path.name}. Review the clock offset.')
    return Clip(path,local,sampled_id(path))


def daily_groups(clips):
    groups=defaultdict(list)
    for clip in sorted(clips,key=lambda c:(c.start,str(c.path).casefold())):
        groups[clip.start.date()].append(clip)
    return [Group(items) for items in groups.values()]
