"""Explicit run requests and read-only camera clearance assessment."""
import argparse
from filesystem_delivery import folders
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
    records = weekly.MANAGER_ROOT/'Records'
    comparison = weekly.camera.read_json(records/'Phase 1 retained pairs content qualification - 2026-11-24.json',{}).get('groups',{})
    keep, _ = weekly.reconciled_keep()
    numbered, _ = weekly.weekly_publications()
    binding = weekly.camera.read_json(Path(weekly.os.environ['LOCALAPPDATA'])/'VlogSync'/'completion-migration-v1.json',{})
    original = weekly.camera.read_json(records/'Runs'/binding.get('run','')/'queue.json',{}).get('rounds',{})
    candidates = {}
    verified_pairs = 0
    for row in audit['groups']:
        day = row['date']
        ids = tuple(row['ordered_identities'])
        evidence = comparison.get(day,{})
        clocks = evidence.get('signatures',{})
        if not all(
            (role in keep.get(ids,{}) and row['recognized'][role]['signature']==clocks.get(role))
            or (numbered.get(ids,{}).get(role,{}).get('method')=='Verified permanent numbered Google Drive publication'
                and original.get(role,{}).get(day,{}).get('signature')==clocks.get(role)
                and clocks.get(role) is not None)
            for role in weekly.exports.ROUND
        ):
            issues.append('Retained pair evidence is incomplete for '+day)
            continue
        clips = evidence.get('clips',{})
        if tuple(clips[name]['identity'] for name in clips)!=ids or not all(
                expected.get(name)==clip['identity'] and clip.get('passed') is True
                for name,clip in clips.items()):
            issues.append('Retained source comparison is incomplete for '+day)
            continue
        verified_pairs += 1
        candidates.update({name:clip['identity'] for name,clip in clips.items()})

    ledger = weekly.numbering.read(weekly.MANAGER_ROOT/'Configuration'/'numbering.json')
    assigned = {row['recording_date']:row for row in ledger['assignments']}
    destinations = folders(weekly)
    verified_days = set(approved)
    for run in sorted((records/'Runs').glob('Production-*')):
        plan = weekly.camera.read_json(run/'plan.json',{})
        queue = weekly.camera.read_json(run/'queue.json',{})
        if plan.get('mode')!='weekly' or not queue.get('publication_finished'):
            continue
        for item in plan.get('projects',[]):
            day, number, ids = item['date'],item['vlog_number'],item['identities']
            reservation = assigned.get(day,{})
            if day in verified_days or (number,weekly.numbering.fingerprint(ids))!=(
                    reservation.get('number'),reservation.get('ordered_identity_sha256')):
                continue
            proof = queue.get('content_evidence',{}).get(day,{})
            if proof.get('identities')!=ids or proof.get('samples_per_clip_per_output',0)<3:
                continue
            source_names = [Path(source['path']).name for source in item['sources']]
            if len(source_names)!=len(ids) or len(set(source_names))!=len(ids) or set(source_names)&expected.keys():
                continue
            for role, folder in destinations.items():
                rendered = queue.get('rounds',{}).get(role,{}).get(day,{})
                delivery = queue.get('delivery',{}).get(role,{}).get(day,{})
                txn = delivery.get('transaction',{})
                path = folder/f'Vlog {number}.mp4'
                sig = rendered.get('signature')
                if (rendered.get('state')!='complete' or not sig
                    or proof.get('signatures',{}).get(role)!=sig or txn.get('signature')!=sig
                    or txn.get('state')!='verified' or Path(delivery.get('destination',''))!=path
                    or not path.is_file() or path.stat().st_size!=sig[0]
                    or reliability.digest(path)!=txn.get('sha256')):
                    issues.append(f'Published {role.lower()} verification unavailable for Vlog {number}.')
                    break
            else:
                verified_days.add(day)
                verified_pairs += 1
                expected.update(zip(source_names,ids,strict=True))
                candidates.update(zip(source_names,ids,strict=True))

    root = weekly.camera_path()
    seen = {p.name:weekly.camera.sampled_id(p) for p in weekly.camera.discover_videos(root)}
    if any(p.is_file() and p.suffix.casefold() in weekly.extra_footage.VIDEO for p in weekly.extra_footage.ROOT.rglob('*')):
        issues.append('Extra Footage must be reconciled before format clearance.')
    dcim=root.parent
    other=sorted(str(p.relative_to(dcim)) for p in dcim.rglob('*') if p.is_file()
                 and not (p.parent==root and p.name in seen) and p.suffix.casefold()!='.lrv')
    misc=sorted(str(p.relative_to(dcim.parent)) for p in (dcim.parent/'MISC').rglob('*') if p.is_file())
    preserved=[]
    for record in (records/'Camera preservation').glob('*/verification.json'):
        catalog=weekly.exports.draft.read(record)
        local=Path.home()/'Pictures'/'VlogSync Camera Preservation'/record.parent.name
        for name in other:
            evidence=catalog.get(Path(name).as_posix())
            copies=(record.parent/name,local/name)
            if evidence and all(p.is_file() and p.stat().st_size==evidence['bytes']
                                and reliability.digest(p)==evidence['sha256'] for p in (dcim/name,*copies)):
                preserved.append(name)
    uncovered=sorted(set(other)-set(preserved))+misc
    if uncovered:
        issues.append('Camera files without verified preservation: '+', '.join(uncovered))
    pending=False
    try:
        weekly.verify_idle_renderer(allow_capcut_previews=False)
        weekly.exports.draft.require_idle(weekly.exports.ROOT)
    except RuntimeError:
        pending=True
    result = reliability.clearance(expected,seen,candidates,issues=issues,native_pending=pending)
    result['content_scope']='Three sampled source windows per retained role plus the baseline timing and integrity audit.'
    result['other_camera_assets']=other+misc
    result['uncovered_camera_assets']=uncovered
    result['other_camera_assets_preserved']=sorted(set(preserved))
    result['disconnect_status'] = ('CapCut or another renderer is open, or a project lock remains. Close it normally before ejecting.' if pending else
                                   'CapCut is closed and the archive renderer is idle. Eject normally.')
    result.update(expected=len(expected),observed=len(seen),camera=str(root),
                  assessed=datetime.now(timezone.utc).isoformat(),production_pairs=verified_pairs)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('pause','resume','cancel','status','camera-assessment','retry-failed','troubleshooting'))
    parser.add_argument('run',nargs='?')
    parser.add_argument('--role',choices=('Master','Proxy'))
    parser.add_argument('--date')
    args = parser.parse_args()
    if args.action=='camera-assessment':
        try:
            result = assess()
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
            report = reliability.diagnostic(error,None)
            result = {'safe_to_disconnect':False,'safe_to_format':False,
                      'issues':['Camera assessment could not verify saved evidence: '+report['message']]}
        print(json.dumps(result,indent=2))
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
