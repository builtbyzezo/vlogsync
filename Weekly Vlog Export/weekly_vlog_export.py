"""Weekly CapCut export. Test mode uses isolated, shorter selections of unmodified originals."""
from __future__ import annotations

import argparse
import datetime as dt
from fractions import Fraction
import hashlib
import json
import math
import msvcrt
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import traceback

HOME = Path(__file__).resolve().parent
sys.path.insert(0, str(HOME.parent/'CapCut Test 3'))
import run_capcut_test3 as exports
from capcut_ui import CapCut
from desktop_controls import Box, WinOcr, pid, process_image

PV = exports.REVIEW.parent
import camera_media as camera
import extra_footage
import deployment
from drive_consolidation import mounted_path, require_ready
from relocated_completion import merge_completed
import numbering
import wve_safety as reliability
from contextlib import ExitStack

MAX_JOBS = 5
RESTART = 75
CURRENT_LOG = None
DRIVE_PROOF = HOME/'drive-publication-evidence.json'
CAMERA_AUDIT = HOME/'camera-reconciliation.json'
PRODUCTION_PREVIEW = HOME/'production-preview.json'
PRODUCTION_OUTPUT = Path.home()/'Videos'/'Weekly Vlog Export'/'Production'
MANAGER_ROOT = None
CAMERA_ROTATION_QUALIFIED = False


def configure_production():
    require_ready()
    global MANAGER_ROOT, DRIVE_PROOF, CAMERA_AUDIT, CAMERA_ROTATION_QUALIFIED
    MANAGER_ROOT, config = deployment.locate()
    extra_footage.ROOT = deployment.under(MANAGER_ROOT.parent,config['extra_footage'])
    if not extra_footage.ROOT.is_dir():
        raise RuntimeError('The configured Extra Footage folder is unavailable')
    DRIVE_PROOF = MANAGER_ROOT/'Records'/'drive-publication-evidence.json'
    CAMERA_AUDIT = MANAGER_ROOT/'Records'/'camera-reconciliation.json'
    evidence = camera.read_json(MANAGER_ROOT/'Records'/'rotation-evidence.json',{})
    CAMERA_ROTATION_QUALIFIED = (evidence.get('status')=='qualified'
                                 and evidence.get('version')==deployment.CAPCUT_BUILD
                                 and {c.get('source_rotation',0)%360 for c in evidence.get('sources',[])}=={90,180})


def publication_path(path):
    return deployment.publication(path,MANAGER_ROOT.parent if MANAGER_ROOT else None)


def event(message):
    line = f'{dt.datetime.now().isoformat(timespec="seconds")} {message}'
    print(line, flush=True)
    if CURRENT_LOG:
        with CURRENT_LOG.open('a', encoding='utf-8') as f:
            f.write(line+'\n')


def save(path, data):
    temp = path.with_name(path.name+'.'+str(os.getpid())+'.new')
    with temp.open('x', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)
    if MANAGER_ROOT and path.name=='queue.json' and path.parent.name.startswith('Production-'):
        try:
            reliability.snapshot(MANAGER_ROOT,path.parent.name,exports.draft.read(path.parent/'plan.json'),data)
        except OSError as error:
            event(f'Local journal saved. Recovery snapshot unavailable: {type(error).__name__}')


def verify_idle_renderer(*, allow_capcut_previews=True, qualification_readers=False):
    out = subprocess.run(['tasklist.exe', '/FI', 'IMAGENAME eq ffmpeg.exe', '/FO', 'CSV', '/NH'],
                         capture_output=True, text=True, check=True, timeout=12)
    if '"ffmpeg.exe"' in out.stdout.casefold():
        if not only_capcut_previews(allow_capcut_previews,qualification_readers):
            raise RuntimeError('An FFmpeg renderer is already active. Finish or pause the other job.')
    legacy=Path(os.environ['LOCALAPPDATA'])/'Programs'/'Process Vlogs 3.7.1'/'processing-session.json'
    session=camera.read_json(legacy,{})
    if session.get('status') == 'running':
        raise RuntimeError('The Process Vlogs journal reports processing is active. Leave it paused.')


def local_lock():
    # One lock for installed Phase 1 controller versions on this Windows account.
    home = Path(os.environ['LOCALAPPDATA'])/'VlogSync'
    home.mkdir(exist_ok=True)
    return home/'controller.lock'


def only_capcut_previews(allow_previews=True,qualification_readers=False):
    query = ("$p=@(Get-CimInstance Win32_Process -Filter \"Name='ffmpeg.exe' OR Name='CapCut.exe' OR Name='python.exe'\" | "
             "Select-Object Name,ProcessId,ParentProcessId,ExecutablePath,CommandLine); ConvertTo-Json -InputObject $p -Compress")
    response = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',query],
                              check=True,capture_output=True,text=True,timeout=30)
    rows = json.loads(response.stdout)
    app = exports.LOCAL/'CapCut'/'Apps'/deployment.CAPCUT_BUILD
    children = [row for row in rows if row['Name'].casefold()=='ffmpeg.exe']
    parents = {row['ProcessId'] for row in rows if Path(row.get('ExecutablePath') or '')==app/'CapCut.exe'}
    auditors = {row['ProcessId'] for row in rows if row['Name'].casefold()=='python.exe'
                and re.search(r'\bretained_audit\.py"?\s*$',row.get('CommandLine') or '',re.I)}
    for row in children:
        command = (row.get('CommandLine') or '').replace('\\','/').casefold()
        if (qualification_readers and row['ParentProcessId'] in auditors
                and Path(row.get('ExecutablePath') or '')==exports.FFMPEG
                and re.search(r' -f (rawvideo|s16le) "?pipe:1"?\s*$',command)):
            continue
        if (not allow_previews or Path(row.get('ExecutablePath') or '')!=app/'ffmpeg.exe' or row['ParentProcessId'] not in parents
                or ' -proxy ' not in command or '/capcut/user data/cache/agencycache/' not in command):
            return False
    return True


def camera_path():
    matches = [p for p in camera.detect_camera_folders() if p.name.lower()=='camera01']
    if len(matches) != 1:
        raise RuntimeError('Expected exactly one connected Insta360 DCIM/Camera01 folder')
    return matches[0]


def published_outputs(config,proof=None):
    """Retain legacy claims. Reuse only separately reconciled Keep or native receipts."""
    claims={}
    for folder in publication_folders(config)['Master']:
        for path in (folder/'.vlog-merger'/'receipts').glob('*.json'):
            receipt=receipt_readonly(path)
            ids=tuple(c.get('identity') for c in receipt.get('clips',[]))
            name=receipt.get('output','')
            if ids and all(ids) and Path(name).name==name:
                claims[ids]={role:[name] for role in exports.ROUND}
    return {},claims


def prior_pairs(config, uncertain=None):
    known, claims = published_outputs(config)
    if uncertain is not None:
        uncertain.update(claims)
    return {key:tuple(row[kind]['path'] for kind in exports.ROUND)
            for key,row in known.items() if set(row)==set(exports.ROUND)}


def publication_folders(config):
    folders = {'Master': set(), 'Proxy': set()}
    if config.get('output_folder'):
        folders['Master'].add(publication_path(config['output_folder']))
    if config.get('editor_proxy',{}).get('output_folder'):
        folders['Proxy'].add(publication_path(config['editor_proxy']['output_folder']))
    for reference in config.get('reference_exports',[]):
        for role,key in (('Master','masters'),('Proxy','proxies')):
            folders[role].add(publication_path(reference[key]))
    return folders


def unassociated_roles(config, label, claims):
    expected = label+'.mp4'
    return {role for role,folders in publication_folders(config).items()
            if any((folder/expected).is_file() for folder in folders) and role not in claims}


def audited_capcut_masters():
    audit = camera.read_json(CAMERA_AUDIT,{})
    root = Path.home()/'Videos'/'CapCut Test 3 Exports'/'Master'
    records = {}
    for row in audit.get('groups',[]):
        proof = row.get('recognized',{}).get('Master',{})
        if not proof or row.get('issues'):
            continue
        path = Path(proof['path'])
        if path.parent==root and path.is_file() and exports.draft.source_stamp(path)==proof.get('signature'):
            records[tuple(row['ordered_identities'])] = proof
    return records


def reconciled_keep():
    """Keep recognition follows the previously verified server object and bytes."""
    known, claims = {}, {}
    if MANAGER_ROOT is None:
        return known, claims
    migration = camera.read_json(MANAGER_ROOT/'Records'/'keep-reconciliation.json',{})
    prior = camera.read_json(MANAGER_ROOT/'Records'/'Previous Drive publication evidence.json',{}).get('files',{})
    current = camera.read_json(DRIVE_PROOF,{})
    live = {publication_path(path):row for path,row in current.get('files',{}).items()
            if current.get('observed',0)>0}
    audit = camera.read_json(CAMERA_AUDIT,{})
    groups = {row['date']:row for row in audit.get('groups',[]) if not row.get('issues')}
    entries = migration.get('rows',[])
    if not migration and not prior and not audit and not current:
        return known, claims
    expected_keys = {(day,role) for day,group in groups.items()
                     for role,proof in group['recognized'].items() if proof.get('sha256')}
    if not prior or not expected_keys or {(row['date'],row['role']) for row in entries}!=expected_keys:
        raise RuntimeError('Drive relocation history is missing or inconsistent. Existing exports must be reconciled.')
    for row in entries:
        role = row['role']
        group = groups.get(row['date'])
        expected = group.get('recognized',{}).get(role,{}) if group else {}
        if not expected.get('sha256') or role not in exports.ROUND:
            continue
        source = prior.get(expected['path'],{})
        folder = MANAGER_ROOT.parent/'Keep'/('1080p' if role=='Master' else '720p')
        legacy_path = folder/(group['label']+'.mp4')
        path, expected_stamp = mounted_path(MANAGER_ROOT.parent, legacy_path, role, expected['sha256'], expected['signature'])
        ids = tuple(group['ordered_identities'])
        # An expired proof or a missing former publication must hold, never reopen work.
        claims.setdefault(ids,{})[role] = str(path)
        record = live.get(legacy_path,{})
        if (source.get('id') and source['id']==record.get('id') and record.get('parent_id')
                and source.get('sha256')==expected['sha256']==record.get('sha256')
                and source.get('size')==record.get('size')==expected['signature'][0]
                and path.is_file() and exports.draft.source_stamp(path)==expected_stamp):
            known.setdefault(ids,{})[role] = dict(path=str(path),sha256=record['sha256'],
                            signature=exports.draft.source_stamp(path),method='Relocated Google Drive server object and SHA-256')
    return known,claims


def weekly_publications():
    """Read prior completed production roles with a matching canonical path and file stamp."""
    known = {}
    versions = {}
    histories = set((HOME/'Runs').glob('Production-*'))
    if MANAGER_ROOT:
        histories.update((MANAGER_ROOT/'Records'/'Runs').glob('Production-*'))
    for folder in sorted(histories):
        plan = camera.read_json(folder/'plan.json',{})
        if plan.get('mode')!='weekly' or Path(plan.get('output',''))!=PRODUCTION_OUTPUT:
            continue
        journal = camera.read_json(folder/'queue.json',{}).get('rounds',{})
        for item in plan.get('projects',[]):
            date = item['date']
            for role in exports.ROUND:
                record = journal.get(role,{}).get(date,{})
                if record.get('state')!='complete':
                    continue
                path = Path(record['metadata']['path'])
                name = (f'Vlog {item["vlog_number"]}.mp4' if item.get('vlog_number') else
                        f'{item.get("output_stem",date)} - {role}.mp4')
                expected = PRODUCTION_OUTPUT/role/name
                if (path==expected or path==Path(record.get('reused_from',''))) and path.is_file() and exports.draft.source_stamp(path)==record['signature']:
                    known.setdefault(tuple(item['identities']),{}).setdefault(role,dict(
                        path=str(path),signature=record['signature'],method='Verified Weekly Vlog Export receipt'))
                    versions.setdefault(date,set()).add(role)
    merge_completed(MANAGER_ROOT, exports.draft.source_stamp, known, versions)
    return known,versions


def receipt_readonly(path):
    try:
        current=path.stat(follow_symlinks=False)
        # Do not hydrate an offline legacy receipt or implicitly trust it for reuse.
        if current.st_size>2097152 or getattr(current,'st_file_attributes',0)&0x441000:
            return {}
        exports.draft.check_path(path)
        return camera.read_json(path,{})
    except (ValueError,OSError,RuntimeError):
        return {}


def choose_groups(folder, args):
    review = camera.read_json(exports.REVIEW,{'groups':[]})
    cfg = camera.read_json(MANAGER_ROOT/'Configuration'/'recording.json',{}) if MANAGER_ROOT else {}
    saved = {g['vlog_date']: g for g in review['groups']}
    if len(saved) != len(review['groups']):
        raise RuntimeError('Multiple saved groups use one vlog date. Review that date before exporting.')
    source = {Path(c['path']).name: c for g in saved.values() for c in g['clips']}
    changed_saved = {day for day,g in saved.items() for c in g['clips']
                     if not (folder/Path(c['path']).name).is_file()
                     or camera.sampled_id(folder/Path(c['path']).name)!=c['identity']}
    def included(day):
        return (not args.start or args.start<=day) and (not args.end or day<=args.end)

    groups = {day: list(g['clips']) for day, g in saved.items() if included(day)}
    discovered = [p for p in camera.discover_videos(folder)
                  if p.parent==folder and re.fullmatch(r'VID_\d{8}_\d{6}_\d+\.mp4',p.name)]
    candidates = [p for p in discovered if p.name not in source
                  and included(dt.date.fromisoformat(p.name[4:12]).isoformat())]
    new = [camera.parse_clip(p, ffprobe=str(exports.PROBE), cfg=cfg) for p in candidates]
    held = [(day,'A saved source is missing or its identity has changed') for day in sorted(changed_saved) if included(day)]
    for group in camera.daily_groups(new):
        day = group.clips[0].start.date().isoformat()
        if not included(day):
            held.append((day, 'Camera timestamp correction moved a recording outside the requested dates'))
            continue
        previous = (dt.date.fromisoformat(day)-dt.timedelta(days=1)).isoformat()
        if (any(c.start.hour < 4 for c in group.clips) and (previous in saved or previous in groups or
                any(c.start.date().isoformat()==previous for c in new))):
            held.append((day, 'New after-midnight recording near another vlog day'))
            continue
        additions = [dict(path=str(c.path), start=c.start.isoformat(), identity=c.identity)
                     for c in group.clips]
        historical = groups.get(day, [])
        order = [row['start'] for row in [*historical, *additions]]
        if order != sorted(order) or any(x['start'] in {c['start'] for c in historical} for x in additions):
            held.append((day, 'New footage would alter a saved order or shares an existing timestamp'))
        else:
            groups[day] = [*historical, *additions]
    extras, external_issues = extra_footage.discover(saved,cfg,exports.draft,exports.PROBE,extra_footage.ROOT)
    unknown_date = False
    changed = set()
    for item in external_issues:
        targets = [date for date in item['dates'] if included(date)]
        if not item['dates']:
            unknown_date = True
        for day in targets or ([] if item['dates'] else ['unknown date']):
            held.append((day,f'{Path(item["path"]).name}: {item["reason"]}'))
    for day, additions in sorted(extras.items()):
        if not included(day):
            continue
        original = groups.get(day,[])
        before = [c['start'] for c in original]
        after = [c['start'] for c in additions]
        all_ids = [c['identity'] for c in [*original,*additions]]
        if (before!=sorted(before) or len(set(after))!=len(after)
                or set(after)&set(before) or len(set(all_ids))!=len(all_ids)):
            held.append((day,'Extra Footage has a timestamp tie, duplicate identity or conflicts with saved order'))
            continue
        groups[day] = sorted([*original,*additions],key=lambda c:c['start'])
        changed.add(day)
    if unknown_date:
        held.extend((day,'Resolve the extra recording with an unknown date before exporting a possible partial vlog')
                    for day in sorted(groups))
    known, claims = published_outputs(cfg)
    keep_known,keep_claims = reconciled_keep()
    capcut_masters = audited_capcut_masters()
    weekly_known,weekly_versions = weekly_publications()
    pending = []
    for day, clips in sorted(groups.items()):
        if any(day == issue[0] for issue in held):
            continue
        identities = tuple(c['identity'] for c in clips)
        existing = dict(known.get(identities,{}))
        existing.update(keep_known.get(identities,{}))
        existing.update(weekly_known.get(identities,{}))
        ambiguous = (set(claims.get(identities,{}))|set(keep_claims.get(identities,{})))-existing.keys()
        original_ids = tuple(c['identity'] for c in saved.get(day,{}).get('clips',[]))
        prior = {} if identities==original_ids else dict(known.get(original_ids,{}))
        if identities!=original_ids:
            prior.update(keep_known.get(original_ids,{}))
        if identities!=original_ids and original_ids in capcut_masters:
            prior.setdefault('Master',capcut_masters[original_ids])
        if day in saved:
            linked = dict(claims.get(identities,{}))
            if day in changed:
                linked.update(prior)
            ambiguous |= unassociated_roles(cfg,saved[day]['label'],linked)
        if 'Master' not in existing and 'Master' not in ambiguous and identities in capcut_masters:
            existing['Master'] = capcut_masters[identities]
        if set(existing)==set(exports.ROUND):
            event(f'{day}: existing recognized Process Vlogs master and proxy; skipped')
        elif ambiguous:
            held.append((day,'Existing '+', '.join(sorted(ambiguous))+' requires identity verification'))
        else:
            if day in changed and (prior or day in weekly_versions):
                event(f'{day}: Extra Footage changes the timeline; existing version stays protected.')
            pending.append((day, clips, existing, day in changed, sorted(set(prior)|weekly_versions.get(day,set()))))
    return pending, held


def camera_check(plan):
    folder = Path(plan['camera'])
    extra = Path(plan.get('extra_folder') or extra_footage.ROOT)
    if not folder.is_dir():
        raise RuntimeError('Insta360 camera disconnected. No more exports will be submitted.')
    for p in plan['projects']:
        for source in p['sources']:
            path = Path(source['path'])
            if (path.parent != folder and not path.is_relative_to(extra)) or not path.is_file():
                raise RuntimeError(f'Missing source for {p["date"]}: {path.name}')
            if path.is_relative_to(extra):
                exports.draft.check_path(path)
            if exports.draft.source_stamp(path) != source['stamp']:
                raise RuntimeError(f'A recording changed since the manifest was created: {path.name}')


def build_plan(args, output):
    folder = camera_path()
    groups, held = choose_groups(folder, args)
    projects = []
    if shutil.disk_usage(Path.home()/'Videos').free < 12*1024**3:
        raise RuntimeError('Less than 12 GiB is free on the output volume')
    for day, clips, existing, changed, prior in groups:
        clips_found = []
        problems = []
        for clip in clips:
            path = Path(clip['path']) if clip.get('external') else folder/Path(clip['path']).name
            try:
                if camera.sampled_id(path)!=clip['identity']:
                    raise RuntimeError('Recording identity changed since selection')
                clips_found.append(clip['media'] if clip.get('external') else
                                   exports.draft.probe(path,exports.PROBE,allow_aac_tail=True,
                                                       metadata=clip.get('doc'),
                                                       allow_camera_rotation=CAMERA_ROTATION_QUALIFIED))
            except (RuntimeError,OSError,ValueError,KeyError,ZeroDivisionError,subprocess.SubprocessError) as error:
                problems.append(f'{path.name}: {error}')
        if problems:
            held.extend((day,reason) for reason in problems)
            continue
        ids = [c['identity'] for c in clips]
        revision = hashlib.sha256(exports.draft.encode(ids)).hexdigest()[:10] if changed else ''
        name = 'WVE '+day.replace('-', '')+(' '+revision if revision else '')
        projects.append(dict(date=day, title=name, sources=clips_found,
                             identities=ids,output_stem=f'{day} - revision {revision}' if revision else day,
                             clips=len(clips), duration_us=sum(c['duration'] for c in clips_found),
                             existing=existing,previous_version_roles=prior,
                             external_count=sum(bool(c.get('external')) for c in clips)))
    return dict(camera=str(folder),extra_folder=str(extra_footage.ROOT),
                projects=projects, output=str(output), held=held,created=time.time(),mode='weekly',
                first_date=args.start,last_date=args.end)


def register(plan, ui):
    root = exports.ROOT
    catalog = exports.draft.read(root/'root_meta_info.json')
    recorded = {r['draft_name']:r for r in catalog['all_draft_store']}
    remaining = []
    for entry in plan['projects']:
        day, name = entry['date'], entry['title']
        work = exports.RUN/day
        target = root/name
        if name in recorded or target.exists():
            saved = work/'prepared.json'
            if name not in recorded or not target.is_dir() or not saved.is_file():
                raise RuntimeError(f'Existing draft {name} cannot be independently associated with this run')
            data = exports.draft.read(saved)
            if recorded[name]['draft_id'] != data['entry']['draft_id']:
                raise RuntimeError(f'Existing draft identity changed: {name}')
            existing = exports.draft.read(target/'draft_content.json')
            media = {c['id']: c['path'] for c in existing['materials']['videos']}
            segments = [s for t in existing['tracks'] if t['type']=='video' for s in t['segments']]
            if [Path(media[s['material_id']]) for s in segments] != [Path(c['path']) for c in entry['sources']]:
                raise RuntimeError(f'Existing draft order or media changed: {name}')
            previous = 0
            for segment, clip in zip(segments, entry['sources'], strict=True):
                source, target = segment['source_timerange'], segment['target_timerange']
                safe_spans = [clip['duration']]
                frame_us = Fraction(100100,3)
                picture_us = clip['frames']*frame_us
                if clip['has_audio'] and 1 < clip['duration']-picture_us <= 2*frame_us:
                    # A qualified AAC tail may snap up to the next 29.97 fps boundary
                    # when CapCut opens the draft. No shorter span is accepted.
                    safe_spans.append(round(math.ceil(clip['duration']/frame_us)*frame_us))
                if (source['start'] != 0 or target['start'] != previous or
                        not any(abs(source['duration']-span)<=1 and abs(target['duration']-span)<=1
                                for span in safe_spans) or segment.get('speed')!=1 or segment.get('volume')!=1
                        or segment['clip'].get('rotation')!=clip['rotation']):
                    raise RuntimeError(f'Existing draft timing changed: {name}')
                previous += target['duration']
        else:
            remaining.append((entry, work))
    if not remaining:
        return
    if ui.windows('CapCut'):
        ui.navigate_home()
        ui.leave_home()
    exports.draft.require_idle(root)
    drafts = []
    for entry, work in remaining:
        work.mkdir(exist_ok=True)
        state = work/'prepared.json'
        if state.is_file():
            check = exports.draft.read(state)
        else:
            names = [c['name'] for c in entry['sources']]
            check = exports.draft.prepare(root, Path(plan['camera']), exports.PROBE, work, names,
                                          entry['title'], keep_order=True, allow_aac_tail=True,
                                          prepared_sources=entry['sources'] if plan.get('mode')=='weekly' else None)
        if check['clips'] != entry['sources']:
            raise RuntimeError(f'Staged references differ from the frozen manifest: {entry["date"]}')
        drafts.append((work, check))
    exports.draft.publish_many(root, drafts, exports.RUN)
    event(f'Registered {len(drafts)} new drafts')


def output_info(path, kind, item):
    data = exports.metadata(path, kind, item['duration_us'])
    video, audio = data['video'], data['audio']
    if Fraction(video['avg_frame_rate']) != Fraction(30000,1001):
        raise RuntimeError(f'Expected 29.97 fps in {path.name}')
    if any(c['has_audio'] for c in item['sources']) and not audio:
        raise RuntimeError(f'Expected audio was missing in {path.name}')
    if any(abs(float(a['duration'])-float(video['duration'])) > .125 for a in audio):
        raise RuntimeError(f'Audio duration differs from video in {path.name}')
    return data


def reconcile(plan, kind, entries, seen):
    for item in plan['projects']:
        day = item['date']
        record = entries.get(day, {})
        proof = item.get('existing',{}).get(kind,{})
        path = Path(proof['path']) if proof else exports.target(day, kind)
        if not path.is_file():
            if record.get('state') == 'complete':
                raise RuntimeError(f'A previously completed output is unavailable: {path}')
            continue
        stat = path.stat()
        signature = [stat.st_size, stat.st_mtime_ns]
        if record.get('state') in ('complete','failed'):
            if signature != record['signature']:
                raise RuntimeError(f'A recorded file changed: {path}')
            continue
        if record.get('state') not in ('triggered', 'queued'):
            raise RuntimeError(f'An output exists without a matching submission: {path}')
        then = seen.get(day)
        if then is None or then[0] != signature:
            seen[day] = (signature, time.monotonic())
            continue
        if stat.st_size < 1 or time.monotonic() - then[1] < 12:
            continue
        try:
            info = output_info(path, kind, item)
        except (subprocess.SubprocessError, ValueError, KeyError):
            continue
        except RuntimeError as error:
            if exports.draft.source_stamp(path)!=signature:
                continue
            entries[day] = dict(record,state='failed',signature=signature,
                                reason=str(error),path=str(path))
            event(f'{kind} {day}: finalized output held for verification review')
            continue
        actual = path.stat()
        if [actual.st_size, actual.st_mtime_ns] != signature:
            continue
        entries[day] = dict(record, state='complete', signature=signature, metadata=info,
                            final_write=actual.st_mtime, verified_at=time.time())
        event(f'{kind} {day}: complete ({stat.st_size:,} bytes)')
    return sum(row['state']=='complete' for row in entries.values())


def reconcile_native(plan, ui, jobs, kind):
    pending = [p for p in plan['projects'] if jobs.get(p['date'],{}).get('state') in ('triggered','queued')
               and not exports.target(p['date'],kind).is_file()]
    if not pending:
        return
    for item in plan['projects']:
        if item['date'] not in jobs and not (exports.RUN/f'{item["date"]}-{kind}-trigger.json').exists():
            ui.cancel_unsubmitted_setup(item)
    view = ui.navigate_home()
    for item in pending:
        rows = ui.home_row(view,item['title'])
        if len(rows)!=1:
            raise RuntimeError(f'Cannot verify native queue state after restart: {item["title"]}')
        text = view.row_text(rows[0]).casefold()
        if 'waiting' not in text and 'exporting' not in text:
            if exports.target(item['date'], kind).is_file():
                continue
            raise RuntimeError(f'Native queue did not confirm {kind} {item["date"]}. No duplicate was submitted.')
        event(f'Recovered existing native submission for {kind} {item["date"]}')


def queue_round(plan, ui, state, journal, kind, test):
    jobs = state['rounds'][kind]
    seen = {}
    if sum(row['state']=='complete' for row in jobs.values())==len(plan['projects']):
        reconcile(plan,kind,jobs,seen)
        return 0
    present = [p for p in plan['projects'] if jobs.get(p['date'],{}).get('state') in ('triggered','queued')
               and exports.target(p['date'],kind).is_file()]
    if present:
        reconcile(plan,kind,jobs,seen)
        time.sleep(12)
        reconcile(plan,kind,jobs,seen)
        save(journal,state)
    for item in plan['projects']:
        day = item['date']
        name = 'Export-'+item['title']
        if (jobs.get(day,{}).get('state') in ('triggered','queued','complete') and ui.windows(name)):
            if jobs[day]['state']=='complete':
                observed = ui.dialog(name)
                if not ui.finished(observed):
                    continue  # The next round may already have this project's setup dialog open.
            screen = ui.queued(None, name)
            path = exports.target(day,kind)
            if ui.finished(screen):
                output_info(path,kind,item)
            if jobs[day]['state'] != 'complete':
                jobs[day]['state'] = 'queued'
            state['max_outstanding'][kind] = max(state['max_outstanding'][kind],1)
            save(journal,state)
            event(f'Recovered the open native progress dialog for {kind} {day}')
            ui.return_home(screen)
            break
    recover_closed_native(plan,ui,state,journal,kind)
    reconcile_native(plan, ui, jobs, kind)
    durations = {item['date']:item['duration_us']/1e6 for item in plan['projects']}
    # A native submission may wait behind several long projects. Bound the round
    # using the approved timeline lengths as well as the standard short-run limit.
    required_time = sum(durations[day] for day in durations if jobs.get(day,{}).get('state')!='complete')
    expires = time.monotonic()+max(6*3600,required_time+3600)
    while time.monotonic() < expires:
        camera_check(plan)
        recover_closed_native(plan,ui,state,journal,kind)
        previous = sum(row['state'] in ('complete','failed') for row in jobs.values())
        count = reconcile(plan, kind, jobs, seen)
        terminal = sum(jobs.get(item['date'],{}).get('state') in ('complete','failed')
                       for item in plan['projects'])
        if terminal != previous:
            save(journal, state)
        new_complete = any(row['state']=='complete' and 'submitted' in row for row in jobs.values())
        if test and kind=='Master' and new_complete and not state.get('restart_exercised'):
            state['restart_exercised'] = True
            save(journal, state)
            event('Controlled controller restart. CapCut and all accepted native jobs remain open.')
            return RESTART
        if terminal == len(plan['projects']):
            return 0
        outstanding = sum(row['state'] in ('triggered','queued') for row in jobs.values())
        request = camera.read_json(journal.with_name('control.json'),{}).get('request')
        if request in ('pause','cancel') and not outstanding:
            event(f'{request.title()} checkpoint reached. CapCut submissions for this round have drained.')
            return 76
        if not request in ('pause','cancel') and outstanding < MAX_JOBS:
            todo = next((p for p in plan['projects'] if p['date'] not in jobs), None)
            if todo:
                verify_idle_renderer(qualification_readers=plan.get('disposable_fixture') is True)
                reliability.power_ready()
                if shutil.disk_usage(Path.home()).free<reliability.MARGIN:
                    raise RuntimeError('The local export volume has reached its free-space reserve')
                path = exports.target(todo['date'], kind)
                try:
                    screen = ui.prepare_export(todo,kind,path)
                except RuntimeError as error:
                    if not str(error).startswith('Timed out waiting for the fully populated CapCut export settings'):
                        raise
                    event(f'{kind} {todo["date"]}: retrying the verified setup once before submission')
                    screen = ui.await_setup('Export-'+todo['title'])
                    screen = ui.prepare_export(todo,kind,path)
                camera_check(plan)
                screen.check()
                if camera.read_json(journal.with_name('control.json'),{}).get('request') in ('pause','cancel'):
                    ui.cancel_unsubmitted_setup(todo)
                    continue
                screen.button('Export', Box(screen.bounds.w*2//3, screen.bounds.h-95,
                                            screen.bounds.w//3, 95))
                day = todo['date']
                retry = bool(state.get('retry_history',{}).get(kind,{}).get(day))
                jobs[day] = dict(state='triggered', submitted=time.time(), title=todo['title'],
                                 retry_count=int(retry))
                save(journal, state)
                exports.confirm_export(day,kind,retry=retry and (exports.RUN/f'{day}-{kind}-trigger.json').exists())
                screen = ui.queued(screen, 'Export-'+todo['title'])
                jobs[day]['state'] = 'queued'
                jobs[day]['native_pid'] = pid(screen.window)
                state['max_outstanding'][kind] = max(state['max_outstanding'][kind], outstanding+1)
                save(journal, state)
                event(f'{kind} {day}: CapCut accepted submission, {outstanding+1} outstanding')
                try:
                    ui.return_home(screen)
                except RuntimeError:
                    if not recover_closed_native(plan,ui,state,journal,kind):
                        raise
                continue
        ordered = sorted((row['submitted'],day) for day,row in jobs.items() if 'submitted' in row)
        deadline = 0
        for submitted,day in ordered:
            deadline = max(deadline,submitted)+max(7200,durations[day]+1800)
            if jobs[day]['state'] in ('triggered','queued') and time.time()>deadline:
                raise RuntimeError(f'The native {kind} submission for {day} exceeded its bounded queue time')
        time.sleep(4)
    raise RuntimeError('The round exceeded its bounded waiting time')


def recover_closed_native(plan,ui,state,journal,kind):
    candidates=[(day,row) for day,row in state['rounds'][kind].items()
                if row['state']=='queued' and not row.get('retry_count')
                and row.get('native_pid') and process_image(row['native_pid']) is None
                and not exports.target(day,kind).exists()]
    if not candidates:
        return False
    if len(candidates)!=1:
        raise RuntimeError('Multiple lost native submissions need operator reconciliation')
    flag=camera.read_json(journal.with_name('control.json'),{}).get('request')
    if flag in ('pause','cancel'):
        raise RuntimeError('The controller is paused; recover the lost native job on explicit resume')
    day,row=candidates[0]
    if not ui.windows('CapCut'):
        ui.launch()
    from crash_recovery import recover_verified_exit
    recover_verified_exit(sys.modules[__name__],ui,plan,state,journal,kind,day,row['native_pid'])
    return True


def pairs(plan, state):
    for item in plan['projects']:
        day = item['date']
        a, b = [state['rounds'][kind][day]['metadata'] for kind in exports.ROUND]
        am, bm = a['video'], b['video']
        def signature(v):
            return (v['nb_frames'], Fraction(v['avg_frame_rate']), Fraction(v['start_time']),
                    int(v['duration_ts'])*Fraction(v['time_base']))
        if signature(am)!=signature(bm):
            raise RuntimeError(f'The finished master and proxy have different video timing: {day}')
        event(f'{day}: pair timing matches, {am["nb_frames"]} frames')


def destinations_first(plan,state):
    for kind in exports.ROUND:
        rows = state['rounds'][kind]
        for item in plan['projects']:
            day = item['date']
            path = exports.target(day,kind)
            reuse = item.get('existing',{}).get(kind)
            if path.exists() and day not in rows and (not reuse or Path(reuse['path'])!=path):
                raise RuntimeError(f'Unassociated existing output preserved before draft registration: {path}')
            if reuse and day not in rows:
                source = Path(reuse['path'])
                stat = source.stat()
                if [stat.st_size,stat.st_mtime_ns]!=reuse['signature'] or (path.exists() and source!=path):
                    raise RuntimeError(f'Previously identified publication needs review: {source}')
                rows[day] = dict(state='complete',signature=reuse['signature'],
                                 metadata=output_info(source,kind,item),reused_from=str(source))
        reconcile(plan,kind,rows,{})
    pending = []
    for item in plan['projects']:
        day = item['date']
        needed = [kind for kind in exports.ROUND
                  if state['rounds'][kind].get(day,{}).get('state') != 'complete']
        event(f'{day}: '+('export '+', '.join(needed) if needed else 'verified pair; skip draft and export'))
        if needed:
            pending.append(item)
    return dict(plan,projects=pending)


def reviewed_selection(plan):
    return {key:plan.get(key) for key in ('camera','extra_folder','output',
                                          'first_date','last_date','projects','held')}


def reviewed_sources(plan):
    selection = reviewed_selection(plan)
    filenames = {'vlog_number','output_stem','title'}
    selection['projects'] = [{key:value for key,value in item.items() if key not in filenames}
                             for item in plan['projects']]
    return selection


def reserve_numbered(first_plan):
    ledger_path = MANAGER_ROOT/'Configuration'/'numbering.json'
    ledger = numbering.read(ledger_path)
    assigned = {row['number'] for row in ledger['assignments']}
    plan = dict(first_plan, projects=numbering.bind(first_plan['projects'],ledger,reserve=True))
    from filesystem_delivery import check_numbers
    numbers = sorted(item['vlog_number'] for item in plan['projects'] if item['vlog_number'] not in assigned)
    if numbers:
        check_numbers(sys.modules[__name__],numbers)
    save(ledger_path,ledger)
    numbering.read(ledger_path)
    save(PRODUCTION_PREVIEW,plan)
    for item in plan['projects']:
        event(f'{item["date"]}: reserved Vlog {item["vlog_number"]}')


def store_production_record(run, plan, state):
    if MANAGER_ROOT is None:
        return
    folder = MANAGER_ROOT/'Records'/'Runs'/run.name
    folder.mkdir(parents=True,exist_ok=True)
    for name,document in (('plan.json',plan),('queue.json',state)):
        target = folder/name
        if target.is_file():
            if exports.draft.read(target)!=document:
                raise RuntimeError(f'Existing Drive run record differs: {target}')
        else:
            exports.draft.write_new(target,exports.draft.encode(document))


def run(args):
    global CURRENT_LOG
    approved = None
    if args.qualification:
        suffix = args.qualification
    else:
        approved = exports.draft.read(PRODUCTION_PREVIEW)
        if approved.get('mode')!='weekly' or Path(approved.get('output',''))!=PRODUCTION_OUTPUT:
            raise RuntimeError('The reviewed production preview is missing or invalid')
        args.start,args.end = approved.get('first_date'),approved.get('last_date')
        key = hashlib.sha256(exports.draft.encode(reviewed_selection(approved))).hexdigest()[:16]
        suffix = 'Production-'+key
    exports.RUN = HOME/'Runs'/suffix
    exports.RUN.mkdir(parents=True, exist_ok=True)
    exports.PLAN = exports.RUN/'plan.json'
    exports.OUTPUT = Path.home()/'Videos'/'Weekly Vlog Export'/suffix if args.qualification else PRODUCTION_OUTPUT
    base = exports.RUN
    CURRENT_LOG = base/'controller.log'
    lock = local_lock().open('a+b')
    power = ExitStack()
    try:
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        if getattr(args,'expect_review',None) and reliability.digest(PRODUCTION_PREVIEW)!=args.expect_review:
            raise RuntimeError('The frozen preview changed after approval')
        verify_idle_renderer()
        if not args.qualification:
            for other in (HOME/'Runs').glob('Production-*'):
                earlier = camera.read_json(other/'queue.json',{})
                if other!=base and earlier and not earlier.get('publication_finished'):
                    raise RuntimeError(f'An earlier local run needs reconciliation: {other.name}')
        power.enter_context(reliability.Awake())
        if args.qualification and not exports.PLAN.is_file():
            raise RuntimeError('The requested isolated qualification fixture plan is missing')
        if exports.PLAN.is_file():
            plan = exports.draft.read(exports.PLAN)
        else:
            plan = build_plan(args,exports.OUTPUT)
            if not args.qualification:
                ledger = numbering.read(MANAGER_ROOT/'Configuration'/'numbering.json')
                plan['projects'] = numbering.bind(plan['projects'],ledger)
            if reviewed_selection(plan)!=reviewed_selection(approved):
                raise RuntimeError('Sources, existing exports or held items changed. Generate and review a new preview.')
            exports.draft.write_new(exports.PLAN,exports.draft.encode(plan))
        if Path(plan['output']) != exports.OUTPUT:
            raise RuntimeError('The manifest destination does not belong to this run')
        if not plan['projects']:
            event(f'No pending jobs. Dates needing review: {plan["held"]}')
            return 0
        journal = base/'queue.json'
        state = exports.draft.read(journal) if journal.is_file() else dict(
            rounds={kind:{} for kind in exports.ROUND}, max_outstanding={kind:0 for kind in exports.ROUND})
        if not (state.get('preset_restored') and all(
                item['date'] in state.get('content_evidence',{}) for item in plan['projects'])):
            camera_check(plan)
        if not args.qualification:
            from filesystem_delivery import preflight,publish
            ledger = numbering.read(MANAGER_ROOT/'Configuration'/'numbering.json')
            if numbering.bind(plan['projects'],ledger)!=plan['projects']:
                raise RuntimeError('Local plan and persistent vlog numbering differ')
            preflight(sys.modules[__name__],plan,state)
        for kind in exports.ROUND:
            (exports.OUTPUT/kind).mkdir(parents=True, exist_ok=True)
        pending = destinations_first(plan,state)
        if any(p.get('existing') for p in plan['projects']):
            save(journal,state)
        if not pending['projects'] and state.get('preset_restored'):
            pairs(plan,state)
            if not args.qualification:
                from content_probe import compare_pair
                for item in plan['projects']:
                    compare_pair(sys.modules[__name__],item,state)
                save(journal,state)
                publish(sys.modules[__name__],plan,state,journal)
                state['publication_finished'] = state.get('publication_finished') or time.time()
                save(journal,state)
                store_production_record(base,plan,state)
                if getattr(args,'after_success',None):
                    verify_idle_renderer(allow_capcut_previews=False)
                    exports.draft.require_idle(exports.ROOT)
                    reliability.completion_power(args.after_success,succeeded=not plan['held'],
                         pending=False,verified_publication=True,saved=True)
            event('Previously completed run verified. CapCut remains untouched.')
            return 0
        with WinOcr() as ocr:
            ui = CapCut(exports, ocr)
            register(pending, ui)
            if not ui.windows('CapCut'):
                ui.launch()
            for kind in exports.ROUND:
                active = plan if kind=='Master' else dict(plan,projects=[
                    item for item in plan['projects']
                    if state['rounds']['Master'].get(item['date'],{}).get('state')=='complete'])
                code = queue_round(active, ui, state, journal, kind, args.qualification)
                if code:
                    return code
            complete = []
            for item in plan['projects']:
                day = item['date']
                if all(state['rounds'][role].get(day,{}).get('state')=='complete' for role in exports.ROUND):
                    try:
                        pairs(dict(plan,projects=[item]),state)
                        if not args.qualification:
                            from content_probe import compare_pair
                            compare_pair(sys.modules[__name__],item,state)
                    except (RuntimeError,OSError,subprocess.SubprocessError) as error:
                        state.setdefault('pair_failures',{})[day] = str(error)
                    else:
                        complete.append(item)
            restore = next((p for p in plan['projects'] if
                            (exports.ROOT/p['title']/'draft_content.json').is_file()),None)
            if restore:
                ui.restore_master(restore)
            state['preset_restored'] = True
            state['complete'] = time.time()
            save(journal, state)
            if getattr(args,'after_success',None) and restore and not plan['held']:
                ui.navigate_home()
                ui.leave_home()
        event('Local exports passed. 1080p HEVC persisted after reopening the export dialog.')
        if not args.qualification:
            publish(sys.modules[__name__],dict(plan,projects=complete),state,journal)
            if len(complete)!=len(plan['projects']):
                raise RuntimeError('Unaffected verified pairs were published. One or more vlogs require review.')
            state['publication_finished'] = time.time()
            save(journal,state)
            store_production_record(base,plan,state)
            if getattr(args,'after_success',None):
                verify_idle_renderer(allow_capcut_previews=False)
                exports.draft.require_idle(exports.ROOT)
                reliability.completion_power(args.after_success,succeeded=not plan['held'],
                     pending=False,verified_publication=True,saved=True)
        elif len(complete)!=len(plan['projects']):
            return 1
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        event(f'HELD: {error}')
        reliability.atomic(base/'troubleshooting.json',reliability.diagnostic(error,base.name))
        with CURRENT_LOG.open('a',encoding='utf-8') as f:
            f.write(traceback.format_exc())
        traceback.print_exc()
        return 1
    finally:
        power.close()
        lock.close()


def ui_preflight():
    verify_idle_renderer()
    exports.RUN = HOME/'Runs'/'Test 4B'
    exports.PLAN = exports.RUN/'plan.json'
    plan = exports.draft.read(exports.PLAN)
    item = next((p for p in plan['projects'] if (exports.ROOT/p['title']/'.locked').is_file()),
                plan['projects'][0])
    before = {p.name:p.stat().st_mtime_ns for p in exports.RUN.glob('*trigger*.json')}
    with WinOcr() as ocr:
        ui = CapCut(exports,ocr)
        if ui.windows('CapCut'):
            ui.navigate_home()
            ui.leave_home()
        ui.launch()
        event('CapCut launched normally and verified Home.')
        ui.restore_master(item)
        event('The exact saved test draft, native export dialog and 1080p HEVC reopen checks passed.')
    after = {p.name:p.stat().st_mtime_ns for p in exports.RUN.glob('*trigger*.json')}
    if after!=before:
        raise RuntimeError('Unexpected export triggers appeared during the preflight')
    event('No export was submitted.')


def preview(args):
    configure_production()
    with local_lock().open('a+b') as lock:
        lock.seek(0)
        msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        verify_idle_renderer()
        plan = build_plan(args,PRODUCTION_OUTPUT)
        ledger = numbering.read(MANAGER_ROOT/'Configuration'/'numbering.json')
        try:
            plan['projects'] = numbering.bind(plan['projects'],ledger)
        except RuntimeError as error:
            event(f'Numbering needs review before this preview can be executed: {error}')
        save(PRODUCTION_PREVIEW,plan)
    for item in plan['projects']:
        needed = [role for role in exports.ROUND if role not in item['existing']]
        event(f'{item["date"]}: {", ".join(needed)} ({item["clips"]} clips, {item["external_count"]} extra)')
    for day,reason in plan['held']:
        event(f'HELD {day}: {reason}')
    event(f'Read-only production plan saved to {PRODUCTION_PREVIEW}.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qualification', nargs='?', const='Test 4',metavar='ISOLATED_RUN')
    parser.add_argument('--from', dest='start')
    parser.add_argument('--to', dest='end')
    parser.add_argument('--preview', action='store_true',help='Make a read-only production checklist without creating projects.')
    parser.add_argument('--ui-preflight', action='store_true',help='Check CapCut startup and export controls with no rendering.')
    parser.add_argument('--execute-preview',action='store_true',help='Render only the previously reviewed production preview.')
    parser.add_argument('--expect-review',help=argparse.SUPPRESS)
    parser.add_argument('--after-success',choices=('sleep','shutdown'))
    parser.add_argument('--reserve-reviewed',action='store_true',help='Reserve numbers for the latest reviewed preview.')
    parser.add_argument('--numbering',action='store_true',help='Show the last assigned date and next vlog number.')
    parser.add_argument('--set-last-number', nargs=2, metavar=('NUMBER','RECORDING_DATE'),
                        help='Edit the number and date anchor without reassigning previously reserved numbers.')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--shortcut', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.expect_review and not (args.reserve_reviewed or args.execute_preview):
        parser.error('The frozen preview digest applies only to reserve or execute.')
    if args.after_success and not args.execute_preview:
        parser.error('--after-success requires --execute-preview.')
    if args.qualification and not re.fullmatch(r'Test [A-Za-z0-9][A-Za-z0-9 -]{0,30}',args.qualification):
        parser.error('An isolated run must begin with Test and use only letters, numbers, spaces and hyphens.')
    if sum(bool(x) for x in (args.qualification,args.preview,args.ui_preflight,args.execute_preview,
                             args.reserve_reviewed,args.numbering,args.set_last_number))!=1:
        parser.error('Choose one operation. See --help.')
    if (args.start or args.end) and not args.preview:
        parser.error('Date ranges belong to --preview. Execution uses the reviewed dates.')
    if args.preview and (args.start or args.end):
        try:
            if not args.start or not args.end or dt.date.fromisoformat(args.start)>dt.date.fromisoformat(args.end):
                raise ValueError()
        except ValueError:
            parser.error('Supply a valid --from YYYY-MM-DD and --to YYYY-MM-DD date range.')
    if args.reserve_reviewed or args.numbering or args.set_last_number:
        configure_production()
        ledger_path = MANAGER_ROOT/'Configuration'/'numbering.json'
        with local_lock().open('a+b') as lock:
            lock.seek(0)
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            if args.reserve_reviewed:
                if args.expect_review and reliability.digest(PRODUCTION_PREVIEW)!=args.expect_review:
                    raise RuntimeError('The preview changed before reservation')
                baseline = exports.draft.read(PRODUCTION_PREVIEW)
                if baseline.get('mode')!='weekly' or Path(baseline.get('output',''))!=PRODUCTION_OUTPUT:
                    raise RuntimeError('A valid reviewed production preview is required before reservation')
                args.start,args.end = baseline.get('first_date'),baseline.get('last_date')
                plan = build_plan(args,PRODUCTION_OUTPUT)
                if reviewed_sources(plan)!=reviewed_sources(baseline):
                    raise RuntimeError('The latest sources or verified outputs no longer match the reviewed batch')
                reserve_numbered(plan)
            elif args.set_last_number:
                ledger = numbering.read(ledger_path)
                numbering.edit_anchor(ledger,int(args.set_last_number[0]),args.set_last_number[1])
                from filesystem_delivery import check_numbers
                check_numbers(sys.modules[__name__],{ledger['last_assigned']['number']+1})
                save(ledger_path,ledger)
            print(json.dumps(numbering.status(numbering.read(ledger_path)),indent=2))
        code = 0
    elif args.ui_preflight:
        ui_preflight()
        code = 0
    elif args.preview:
        preview(args)
        code = 0
    elif args.qualification and not args.worker:
        command = [sys.executable,'-B',str(Path(__file__)), '--qualification',args.qualification,'--worker']
        code = subprocess.call(command)
        if code == RESTART:
            code = subprocess.call(command)
    else:
        if args.execute_preview:
            configure_production()
        code = run(args)
    if args.shortcut:
        input('Press Enter to close this report. CapCut is left open. ')
    return code


if __name__ == '__main__':
    # Helpers share this process's configured mount and run context.
    sys.modules['weekly_vlog_export'] = sys.modules[__name__]
    sys.exit(main())
