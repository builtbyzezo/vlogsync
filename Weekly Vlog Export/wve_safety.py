"""Filesystem transactions and conservative Phase 1 safety checks."""
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from uuid import uuid4

VERSION = '1.1.0-rc3'
MARGIN = 12*1024**3


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def identity(path):
    info = path.stat()
    return [info.st_size,info.st_mtime_ns]


def atomic(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp = path.with_name(f'.{path.name}.{uuid4().hex}.new')
    with tmp.open('xb') as stream:
        stream.write(json.dumps(data,indent=2).encode())
        stream.flush()
        os.fsync(stream.fileno())
    tmp.replace(path)


def snapshot(root,run,plan,state):
    folder = root/'Records'/'Recovery snapshots'/run
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    atomic(folder/(stamp+'.json'),dict(schema=1,plan=plan,queue=state))


def publish_file(source,final,partial,record,write):
    """Record intent before a no-replace commit. Resume only a validated prefix."""
    expected = identity(source)
    if not record:
        checksum = digest(source)
        if expected!=identity(source):
            raise RuntimeError('Verified local file changed during fingerprinting')
        record.update(signature=expected,sha256=checksum,state='staging')
        write()
    if record['signature']!=expected:
        raise RuntimeError('Verified local file changed')
    if final.is_symlink() or partial.is_symlink():
        raise RuntimeError('Linked publication target is unsafe')
    if final.exists():
        if record['state'] not in ('committing','verified') or final.stat().st_size!=expected[0] or digest(final)!=record['sha256']:
            raise FileExistsError(final)
        if partial.exists() and record['state']=='committing':
            raise RuntimeError('Both staged and final files exist. Resolve the collision before resuming.')
    else:
        if record['state']=='verified':
            raise RuntimeError('Previously verified destination is unavailable')
        offset = partial.stat().st_size if partial.exists() else 0
        remaining = expected[0]-offset
        if remaining<0:
            raise RuntimeError('Publication checkpoint is too large')
        for location in (final.parent,Path.home()):
            if shutil.disk_usage(location).free < remaining+MARGIN:
                raise RuntimeError('Insufficient physical or destination storage for publication')
        with source.open('rb') as original:
            if offset:
                with partial.open('rb') as prior:
                    while block := prior.read(8*1024**2):
                        if original.read(len(block))!=block:
                            raise RuntimeError('Publication checkpoint has a different prefix')
            with partial.open('ab' if partial.exists() else 'xb') as destination:
                shutil.copyfileobj(original,destination,8*1024**2)
                destination.flush()
                os.fsync(destination.fileno())
        if identity(source)!=expected or digest(partial)!=record['sha256']:
            raise RuntimeError('Publication staging verification failed')
        record['state']='committing'
        write()
        # Windows rename fails if a target exists. Keep the staging copy on collisions.
        if os.name!='nt':
            raise RuntimeError('This publication implementation requires Windows no-replace rename')
        partial.rename(final)
        if digest(final)!=record['sha256']:
            raise RuntimeError('Final destination verification failed')
    record['state']='verified'
    write()


def matches_sample(expected,actual,threshold=12):
    """Relative luminance check. Similar darkness or stillness does not fail by itself."""
    if len(expected)!=len(actual) or not expected:
        return False
    return sum(abs(x-y) for x,y in zip(expected,actual))/len(expected)<=threshold


def clearance(expected,observed,proofs,*,issues=(),native_pending=False):
    missing = set(expected)-set(observed)
    changed = {key for key in expected.keys() & observed.keys() if expected[key]!=observed[key]}
    extra = set(observed)-set(expected)
    unverified = {key for key in expected if proofs.get(key)!=expected[key]}
    return dict(safe_to_disconnect=not native_pending,
                safe_to_format=bool(expected) and not any((missing,changed,extra,unverified,issues,native_pending)),
                missing=sorted(missing),changed=sorted(changed),extra=sorted(extra),
                missing_strong_content_proof=sorted(unverified),issues=list(issues))


def diagnostic(error,run):
    message = re.sub(r'(?i)(?:[A-Z]:[\\/][^\r\n]+|[\w.+-]+@[\w.-]+\.[a-z]{2,})','[private path or account]',str(error))
    return dict(action='Copy Troubleshooting Prompt for ChatGPT',controller=VERSION,
                system=os.name,capcut='9.8.0.4213',run=run,operation='production controller',
                error=type(error).__name__,message=message,affected_vlog='See the local run journal',
                recovery='Keep journals and outputs. Reconcile CapCut queue before retrying.')


class Awake:
    def __enter__(self):
        if not ctypes.windll.kernel32.SetThreadExecutionState(0x80000001):
            raise RuntimeError('Windows declined the temporary system power request')
        return self

    def __exit__(self,*_):
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)


class PowerStatus(ctypes.Structure):
    _fields_ = [('ac',ctypes.c_ubyte),('flag',ctypes.c_ubyte),('percent',ctypes.c_ubyte),
                ('reserved',ctypes.c_ubyte),('life',ctypes.c_ulong),('full',ctypes.c_ulong)]


def power_ready():
    status = PowerStatus()
    if (ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)) and
            status.ac==0 and status.percent<=15):
        raise RuntimeError('Low battery. Connect AC power before submitting another export.')


def completion_power(action, *, succeeded, pending, verified_publication, saved):
    """An explicit one-run action. Nothing changes Windows power policy."""
    if action not in ('sleep','shutdown'):
        raise ValueError('Choose sleep or shutdown')
    if not succeeded or pending or not verified_publication or not saved:
        raise RuntimeError('Power action held until the successful run is saved and publication verified')
    if action=='sleep':
        suspend=ctypes.windll.powrprof.SetSuspendState
        suspend.argtypes=(ctypes.c_ubyte,)*3
        suspend.restype=ctypes.c_ubyte
        if not suspend(False,False,False):
            raise RuntimeError('Windows declined sleep')
    else:
        subprocess.run(['shutdown.exe','/s','/t','0'],check=True,timeout=15)


def safe_retry(state,role,day,directory,filename):
    """One retry of an explicitly failed native job with no output to overwrite."""
    jobs=state['rounds'][role]
    record=jobs.get(day,{})
    if record.get('state')!='failed' or record.get('retry_count',0):
        raise RuntimeError('Only a confirmed failed job can receive one explicit retry')
    if not directory.is_dir() or (directory/filename).exists():
        raise RuntimeError('No retry while the destination is unavailable or an existing file needs review')
    if any(p.is_file() and p.suffix.lower() in ('.mp4','.tmp','.partial') and
           not re.fullmatch(r'(?:Vlog \d+|\d{4}-\d{2}-\d{2} - (?:Master|Proxy))\.mp4',p.name)
           for p in directory.iterdir()):
        raise RuntimeError('An unidentified native partial must be reconciled before retrying')
    state.setdefault('retry_history',{}).setdefault(role,{})[day]=dict(record,retry_count=1)
    del jobs[day]
