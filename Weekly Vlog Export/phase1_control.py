"""Explicit run requests and read-only camera clearance assessment."""
import argparse
from deployment import historical_run
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time

import wve_safety as reliability
import weekly_vlog_export as weekly


def assess():
    weekly.configure_production()
    audit = weekly.exports.draft.read(weekly.CAMERA_AUDIT)
    reviewed = weekly.exports.draft.read(weekly.exports.REVIEW)['groups']
    approved = {row['date']:row['ordered_identities'] for row in audit['groups']}
    expected = {}
    issues = []
    if len(reviewed)!=len(approved):
        issues.append('The review and approved date counts differ.')
    for group in reviewed:
        if [clip['identity'] for clip in group['clips']]!=approved.get(group['vlog_date']):
            issues.append('Saved order differs from verified date '+group['vlog_date'])
        for clip in group['clips']:
            name = Path(clip['path']).name
            if name in expected:
                issues.append('Duplicate saved source '+name)
            expected[name] = clip['identity']
    if len(expected)!=audit['recordings']:
        issues.append('Approved inventory count differs.')
    root = weekly.camera_path()
    seen = {p.name:weekly.camera.sampled_id(p) for p in weekly.camera.discover_videos(root)
            if p.suffix.lower()=='.mp4' and p.name.upper().startswith('VID_')}
    if any(p.suffix.casefold() in weekly.extra_footage.VIDEO for p in weekly.extra_footage.ROOT.rglob('*')):
        issues.append('Extra Footage must be reconciled before format clearance.')
    dcim=root.parent
    other=sorted(str(p.relative_to(dcim)) for p in dcim.rglob('*') if p.is_file()
                 and p.name not in seen and p.suffix.casefold()!='.lrv')
    preserved=[]
    for record in (weekly.MANAGER_ROOT/'Records'/'Camera preservation').glob('*/verification.json'):
        catalog=weekly.exports.draft.read(record)
        local=Path.home()/'Pictures'/'VlogSync Camera Preservation'/record.parent.name
        for name in other:
            evidence=catalog.get(Path(name).as_posix())
            copies=(record.parent/name,local/name)
            if evidence and all(p.is_file() and p.stat().st_size==evidence['bytes']
                                and reliability.digest(p)==evidence['sha256'] for p in (dcim/name,*copies)):
                preserved.append(name)
    if set(other)-set(preserved):
        issues.append('One or more non-vlog camera files lack verified preservation copies.')
    comparison=weekly.camera.read_json(weekly.HOME/'Retained pair content audit.json',{})
    records=weekly.MANAGER_ROOT/'Records'
    run=records/'Runs'/historical_run(weekly.MANAGER_ROOT)
    queue=weekly.camera.read_json(run/'queue.json',{}).get('rounds',{})
    keep={(row['date'],row['role']):weekly.publication_path(row['keep_path'])
          for row in weekly.camera.read_json(records/'keep-reconciliation.json',{}).get('rows',[])}
    candidates={}
    for day,group in comparison.get('groups',{}).items():
        paths={role:Path(queue.get(role,{}).get(day,{}).get('metadata',{}).get('path')
                         or keep.get((day,role),'.')) for role in weekly.exports.ROUND}
        if not all(path.is_file() and weekly.exports.draft.source_stamp(path)==group['signatures'].get(role)
                   for role,path in paths.items()):
            issues.append(f'Retained comparison file stamp differs for {day}.')
            continue
        candidates.update({name:row['identity'] for name,row in group['clips'].items() if row.get('passed')})
    if len(candidates)!=len(expected):
        issues.append('Not every retained segment passed source-to-output comparison.')
    pending=False
    try:
        weekly.verify_idle_renderer(allow_capcut_previews=False)
        weekly.exports.draft.require_idle(weekly.exports.ROOT)
    except RuntimeError:
        pending=True
    result = reliability.clearance(expected,seen,candidates,issues=issues,native_pending=pending)
    result['content_scope']='Three sampled source windows per retained role plus the baseline timing and integrity audit.'
    result['other_camera_assets']=other
    result['other_camera_assets_preserved']=sorted(set(preserved))
    result['disconnect_status'] = ('Active or unverified camera readers remain.' if pending else
                                   'CapCut is closed and the archive renderer is idle. Eject normally.')
    result.update(expected=len(expected),observed=len(seen),camera=str(root),
                  assessed=datetime.now(timezone.utc).isoformat(),production_pairs=len(approved))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('pause','resume','cancel','status','camera-assessment','retry-failed','troubleshooting'))
    parser.add_argument('run',nargs='?')
    parser.add_argument('--role',choices=('Master','Proxy'))
    parser.add_argument('--date')
    args = parser.parse_args()
    if args.action=='camera-assessment':
        print(json.dumps(assess(),indent=2))
        return
    if not args.run or not re.fullmatch(r'Production-[a-f0-9]{16}',args.run):
        parser.error('Supply the exact local production run ID.')
    base = weekly.RUN_ROOT/args.run
    if not (base/'plan.json').is_file():
        parser.error('The nominated local candidate run does not exist.')
    if args.action=='troubleshooting':
        record=weekly.exports.draft.read(base/'troubleshooting.json')
        print('Copy Troubleshooting Prompt for ChatGPT')
        print(json.dumps(record,indent=2))
    elif args.action=='retry-failed':
        if not args.role or not args.date:
            parser.error('Retry requires the exact role and recording date.')
        with weekly.local_lock().open('a+b') as lock:
            lock.seek(0)
            weekly.msvcrt.locking(lock.fileno(),weekly.msvcrt.LK_NBLCK,1)
            weekly.verify_idle_renderer(allow_capcut_previews=False)
            weekly.exports.draft.require_idle(weekly.exports.ROOT)
            plan=weekly.exports.draft.read(base/'plan.json')
            item=next((p for p in plan['projects'] if p['date']==args.date),None)
            if not item or not item.get('vlog_number'):
                parser.error('No reserved vlog for this date.')
            journal=base/'queue.json'
            state=weekly.exports.draft.read(journal)
            if args.date in state.get('retry_history',{}).get(args.role,{}):
                raise RuntimeError('The one retry has already been reserved.')
            reliability.safe_retry(state,args.role,args.date,Path(plan['output'])/args.role,
                                   f'Vlog {item["vlog_number"]}.mp4')
            reliability.atomic(journal,state)
            print('Retry reserved. Launch the same frozen preview to submit it.')
    elif args.action=='status':
        data = weekly.camera.read_json(base/'queue.json',{})
        print(json.dumps(dict(rounds={role:{day:r['state'] for day,r in jobs.items()}
                                     for role,jobs in data.get('rounds',{}).items()},
                              request=weekly.camera.read_json(base/'control.json',{})),indent=2))
    else:
        reliability.atomic(base/'control.json',dict(request=args.action,time=time.time()))
        print('Recorded request. A request does not establish that CapCut has stopped.')


if __name__=='__main__':
    main()
