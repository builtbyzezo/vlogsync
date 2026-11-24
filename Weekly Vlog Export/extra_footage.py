"""Metadata-only discovery and conservative assignment of external camera files."""
from datetime import datetime, timedelta
from pathlib import Path
import os
import subprocess
from zoneinfo import ZoneInfo

import vlog_core as camera


ROOT = Path.home()/'Videos'/'Extra Footage'
VIDEO = {'.mp4','.mov','.m4v','.mkv','.webm','.avi','.mts','.m2ts','.insv','.360'}


def recording_time(doc, filename, zone):
    """Require timezone-aware capture metadata and report contradictory clocks."""
    tags = [{str(k).casefold():v for k,v in item.get('tags',{}).items()}
            for item in (doc['format'],*doc['streams'])]
    values = [m[k] for m in tags for k in ('com.apple.quicktime.creationdate','creation_time')
              if m.get(k)]
    parsed = []
    for value in values:
        try:
            stamp = datetime.fromisoformat(str(value).replace('Z','+00:00'))
            if stamp.tzinfo is None:
                raise ValueError('no timezone')
        except ValueError as error:
            raise RuntimeError('Recording metadata omits a trustworthy timestamp or timezone') from error
        parsed.append(stamp)
    if not parsed:
        raise RuntimeError('No timezone-aware recording timestamp found')
    if max(parsed)-min(parsed)>timedelta(seconds=3):
        raise RuntimeError('Embedded recording timestamps disagree')
    local = parsed[0].astimezone(zone)
    start = local.replace(tzinfo=None)
    if not 2000<=start.year or local>datetime.now(zone)+timedelta(days=1):
        raise RuntimeError('Capture timestamp is outside a plausible recording date')
    if start.replace(tzinfo=zone,fold=0).utcoffset()!=start.replace(tzinfo=zone,fold=1).utcoffset():
        raise RuntimeError('The recording falls in an ambiguous daylight-saving clock hour')
    named = camera.filename_time(filename)
    if named and abs(named-start)>timedelta(minutes=2):
        raise RuntimeError('The timestamped filename disagrees with capture metadata')
    return start


def membership(start, timelines):
    physical = start.date().isoformat()
    before = (start.date()-timedelta(days=1)).isoformat()
    containing = [day for day,(first,last) in timelines.items() if first<=start<last]
    if len(containing)>1:
        return None,containing,'The recording overlaps more than one saved vlog'
    if containing:
        return containing[0],[],None
    if start.hour<4 and before in timelines:
        return None,[before,physical],'After-midnight boundary is not confirmed by a saved group'
    return physical,[],None


def discover(saved, config, draft, binary, folder=ROOT):
    if not folder.is_dir():
        return {},[]
    draft.check_path(folder)
    files = []
    def failed(error):
        raise error
    for root,dirs,names in os.walk(folder,followlinks=False,onerror=failed):
        here = Path(root)
        dirs[:] = [name for name in dirs if not ((here/name).is_symlink() or (here/name).is_junction())]
        files.extend(here/name for name in names if Path(name).suffix.lower() in VIDEO
                     and not name.upper().startswith(('LRV_','THM_')))
    if not files:
        return {},[]
    zone = ZoneInfo(config.get('timezone','America/Toronto'))
    timelines = {}
    for day,group in saved.items():
        ranges = [(datetime.fromisoformat(c['start']),float(c['duration'])) for c in group['clips']]
        if ranges:
            timelines[day] = (min(s for s,_ in ranges),max(s+timedelta(seconds=d) for s,d in ranges))
    found = {}
    issues = []
    for path in sorted(files):
        day,start = None,None
        guesses = []
        try:
            draft.check_path(path)
            stamp = draft.source_stamp(path)
            doc = camera.probe(path,ffprobe=str(binary))
            start = recording_time(doc,path,zone)
            day,guesses,issue = membership(start,timelines)
            if issue:
                raise RuntimeError(issue)
            media = draft.probe(path,binary,external=True,metadata=doc,allow_aac_tail=True)
            identity = camera.sampled_id(path)
            if stamp!=media['stamp'] or stamp!=draft.source_stamp(path):
                raise RuntimeError('Recording changed while it was inspected')
        except (RuntimeError,OSError,ValueError,KeyError,ZeroDivisionError,subprocess.SubprocessError) as error:
            hint = start or camera.filename_time(path)
            when = guesses or ([day] if day else [])
            if not when and hint:
                hinted_day, nearby, _ = membership(hint,timelines)
                when = sorted({hint.date().isoformat(), *(nearby or [hinted_day])})
            issues.append(dict(path=str(path),dates=when,reason=str(error)))
            continue
        found.setdefault(day,[]).append(dict(path=str(path),start=start.isoformat(),identity=identity,
                                            duration=media['duration']/1e6,external=True,media=media))
    return found,issues
