"""Local editor handoff. Space transfers are performed and confirmed by the operator."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from panel import ENGINE, atomic, read, sealed

DEFAULTS = {'space':'builtbyzezo','upload':'New Vlogs ⏩','completed':'Edited ✅'}
PHASE3 = ENGINE.parent/'interface'/'Runs'/'Editor handoff'


def store(panel):
    import hashlib
    workspace_key=hashlib.sha256(str(panel.root).casefold().encode()).hexdigest()[:16]
    if not panel.live:
        root=panel.root
    elif panel.native_pilot:
        root=PHASE3/workspace_key
    else:
        root=Path(os.environ['LOCALAPPDATA'])/'VlogSync'/'Editor handoff'/workspace_key
    root.mkdir(parents=True, exist_ok=True)
    return root/'editor-handoff.json'


def settings(panel):
    return DEFAULTS | read(panel.config,{}).get('space',{})


def engine(panel):
    os.environ['WEEKLY_VLOG_MANAGER_ROOT'] = str(panel.root)
    if panel.native_pilot:
        os.environ['VLOGSYNC_DISPOSABLE_NATIVE'] = str(panel.root)
    import weekly_vlog_export as w
    if panel.native_pilot and w.PILOT_WORKSPACE != panel.root:
        raise RuntimeError('Native editor qualification is outside its disposable workspace.')
    w.configure_production()
    return w


def inventory(panel):
    if not panel.live:
        return {n:{'n':n,'date':'2026-11-25'} for n in range(281,288)}
    w=engine(panel)
    from filesystem_delivery import folders
    ledger=w.numbering.read(panel.ledger)
    dates={r['recording_date']:r for r in ledger['assignments']}
    result={}
    records=panel.root/'Records'/'Runs'
    known,_=w.weekly_publications()
    keep,_=w.reconciled_keep()
    for row in read(w.CAMERA_AUDIT,{}).get('groups',[]):
        day=row['date']; ids=row['ordered_identities']; assignment=dates.get(day,{})
        if assignment.get('ordered_identity_sha256')!=w.numbering.fingerprint(ids):
            continue
        roles=keep.get(tuple(ids),{}) | known.get(tuple(ids),{})
        if all(roles.get(r,{}).get('sha256') for r in ('Master','Proxy')):
            n=assignment['number']
            result[n]=dict(n=n,date=day,files={r:dict(path=roles[r]['path'],sha256=roles[r]['sha256']) for r in ('Master','Proxy')})
    for run in records.glob('Production-*'):
        queue=read(run/'queue.json',{})
        if not queue.get('publication_finished'):
            continue
        for job in read(run/'plan.json',{}).get('projects',[]):
            day=job['date']; n=job.get('vlog_number'); assignment=dates.get(day,{})
            if n!=assignment.get('number') or not n or assignment.get('ordered_identity_sha256')!=w.numbering.fingerprint(job['identities']):
                continue
            files={}
            for role,folder in folders(w).items():
                row=queue.get('delivery',{}).get(role,{}).get(day,{})
                tx=row.get('transaction',{})
                path=folder/f'Vlog {n}.mp4'
                if tx.get('state')!='verified' or not tx.get('sha256') or Path(row.get('destination',''))!=path or not path.is_file() or path.stat().st_size!=tx['signature'][0]:
                    break
                files[role]=dict(path=str(path),sha256=tx['sha256'])
            if len(files)==2:
                result[n]=dict(n=n,date=day,files=files)
    return result


def confirmed(panel, number):
    if not panel.live:
        return number==281
    receipt=read(panel.root/'Records/Phase 3 Qualification'/f'Vlog {number} editor handoff.json',{})
    return receipt.get('status')=='Confirmed manual submission' and receipt.get('project')==f'Vlog {number}'


def media_digest(file):
    from wve_safety import digest
    return digest(file)


def require_hash(file, expected):
    if media_digest(file)!=expected:
        raise ValueError('A verified media file changed. Review its receipt before proceeding.')


def revision(draft):
    import hashlib
    documents=[]
    paths = [*draft.rglob('draft_content.json'), *draft.rglob('attachment_editing.json')]
    if (draft/'Timelines/project.json').is_file():
        paths.append(draft/'Timelines/project.json')
    for path in sorted(paths):
        doc=read(path)
        # CapCut's native Duplicate updates the draft format version and timestamps.
        for volatile in ('update_time','create_time','last_open_time','new_version'):
            doc.pop(volatile,None)
        # CapCut 9.8 writes an explicit default when opening legacy audio.
        # Channel assignments and every other mapping value remain revision-bound.
        for mapping in doc.get('materials',{}).get('sound_channel_mappings',[]):
            if mapping.get('type')=='':
                mapping['type']='none'
        documents.append((path.relative_to(draft).as_posix(),doc))
    if not documents:
        raise ValueError('The draft has no readable timeline.')
    return hashlib.sha256(json.dumps(documents,sort_keys=True).encode()).hexdigest()


PROJECT_NAME = re.compile(r'Vlog (?P<number>[1-9][0-9]*)(?: EDITED)?(?: \([0-9]+\))?(?:-copy)?',re.I)
RETURN_STATES = ('submitted','downloaded')


def project_number(name):
    match=PROJECT_NAME.fullmatch(name)
    return int(match['number']) if match else None


def local_draft(root, name):
    if not isinstance(name,str) or not project_number(name):
        raise ValueError('Use an exact numbered CapCut project name.')
    row=[x for x in read(root/'root_meta_info.json',{})['all_draft_store'] if x['draft_name']==name]
    path=root/name
    if len(row)!=1 or Path(row[0]['draft_fold_path']).resolve()!=path.resolve() or not path.is_dir() or path.is_symlink():
        raise ValueError('The exact local project is unavailable or ambiguous.')
    return path,row[0]


def media_binding(draft, doc, *, allow_missing=False, approved_audio=None):
    number=project_number(draft.name)
    bindings={}
    for video in doc['materials']['videos']:
        value=video.get('path','')
        if not value:
            continue
        match=re.fullmatch(r'##_draftpath_placeholder_[A-Fa-f0-9-]+_##/(.+)',value)
        rel=Path(match[1]) if match else None
        if rel is None or rel.anchor or ':' in str(rel) or '..' in rel.parts:
            raise ValueError('Unexpected external video reference. Review this project manually.')
        candidate=draft/rel
        if candidate.resolve()!=candidate.absolute():
            raise ValueError('A bundled video is redirected outside its expected path.')
        if rel.name.casefold()==f'vlog {number}.mp4':
            bindings.setdefault(candidate,[]).append(video)
        elif not candidate.is_file():
            raise ValueError('An additional video asset is missing. Restore it before preparing the edit.')
    for audio in doc['materials'].get('audios',[]):
        value=audio.get('path','')
        if value:
            if approved_audio and value in approved_audio:
                external=Path(value)
                if not external.is_absolute() or external.is_symlink() or not external.is_file():
                    raise ValueError('An approved editorial audio file is unavailable.')
                require_hash(external,approved_audio[value])
                continue
            match=re.fullmatch(r'##_draftpath_placeholder_[A-Fa-f0-9-]+_##/(.+)',value)
            rel=Path(match[1]) if match else None
            if (rel is None or rel.anchor or ':' in str(rel) or '..' in rel.parts
                    or not (draft/rel).is_file() or (draft/rel).resolve()!=(draft/rel).absolute()):
                raise ValueError('An additional audio asset is unavailable. Restore it before preparing the edit.')
    if len(bindings)!=1:
        raise ValueError('The numbered proxy reference is missing or ambiguous.')
    file, videos=next(iter(bindings.items()))
    if not allow_missing and not file.is_file():
        raise ValueError('The bundled media is unavailable.')
    return file,videos


def media_path(draft, doc):
    return media_binding(draft, doc)[0]


def historical_pair(number, destinations, archive):
    files={}
    for role,dimensions in (('Master',(1920,1080)),('Proxy',(1280,720))):
        path=destinations[role]/f'Vlog {number}.mp4'
        if not path.is_file():
            return {}
        size=path.stat().st_size
        matching=set()
        for row in archive:
            metadata=row.get('videoMediaMetadata',{})
            checksum=row.get('sha256Checksum','')
            if (row.get('name','').casefold()==path.name.casefold() and row.get('trashed') is False
                    and (metadata.get('width'),metadata.get('height'))==dimensions
                    and str(row.get('size'))==str(size) and re.fullmatch(r'[a-f0-9]{64}',checksum)):
                matching.add(checksum)
        if len(matching)!=1:
            return {}
        files[role]={'path':str(path),'sha256':next(iter(matching))}
    return files


def discover_historical(panel, state, entries, known):
    if not panel.live or panel.native_pilot:
        return
    w=engine(panel)
    from filesystem_delivery import folders
    archive=read(panel.root/'Records/Historical Drive archive inventory.json',{}).get('files',[])
    historical=w.numbering.read(panel.ledger)['observed_through']
    destinations=folders(w)
    for entry in entries:
        name=entry.get('draft_name','')
        n=project_number(name)
        if not n or name.casefold().endswith('-copy') or entry.get('draft_cloud_last_action_download') is not True:
            continue
        if str(n) in state and state[str(n)]['state']!='history-held':
            continue
        row=known.get(n)
        if row is not None:
            state[str(n)]=row | {'state':'downloaded'}
        elif n<=historical:
            files=historical_pair(n,destinations,archive)
            state[str(n)]={'n':n,'state':'downloaded' if files else 'history-held','historical':True,
                           'files':files,'retrieval_error':None if files else 'Verified archive pair unavailable or ambiguous.'}


def timing(w, path):
    from fractions import Fraction
    def timeline(stream):
        result=json.loads(subprocess.check_output([str(w.exports.PROBE),'-v','error','-select_streams',
            stream,'-show_streams','-show_packets','-show_entries','stream=codec_name,width,height,time_base,sample_rate,channels:packet=pts,duration',
            '-of','json',str(path)],timeout=600))
        info,=result['streams']
        base=Fraction(info['time_base'])
        return info,sorted((int(row['pts'])*base,int(row['duration'])*base) for row in result['packets'])
    return timeline('v:0'),timeline('a:0')


def validate_pair(w, files, *, historical=False):
    from fractions import Fraction
    for row in files.values():
        require_hash(Path(row['path']),row['sha256'])
    (m,mt),(a,at)=timing(w,Path(files['Master']['path']))
    (p,pt),(b,bt)=timing(w,Path(files['Proxy']['path']))
    if ((m.get('codec_name'), m.get('width'),m.get('height'))!=('hevc',1920,1080)
        or (p.get('codec_name'),p.get('width'),p.get('height'))!=('h264',1280,720)
        or not mt or mt!=pt or not at or not bt
        or (a.get('codec_name'),b.get('codec_name'))!=('aac','aac') or a.get('channels')!=b.get('channels')):
        raise ValueError('Master and proxy picture timing or media profiles differ.')
    needs_audio = at!=bt or a['sample_rate']!=b['sample_rate']
    if needs_audio:
        # Legacy encodes may use different AAC sample rates. Do not replace editor
        # audio in that case. The proxy's packet timing and bytes must survive the mux.
        if not historical or abs((at[-1][0]+at[-1][1])-(bt[-1][0]+bt[-1][1]))>Fraction(1,1000):
            raise ValueError('Master and proxy audio timing is incompatible.')
    return round((pt[-1][0]+pt[-1][1])*1_000_000),needs_audio


def staged_legacy_sources(files, proof):
    """Verify one local copy of each large archived input for repeatable qualification."""
    from wve_safety import MARGIN
    directory=proof/'Verified inputs'
    directory.mkdir(parents=True,exist_ok=True)
    staged={}
    for role,row in files.items():
        path=directory/f'{role}.mp4'
        source=Path(row['path'])
        if not path.exists():
            partial=path.with_suffix('.partial')
            if partial.exists():
                # Continue only after comparing the saved prefix to the source.
                offset=partial.stat().st_size
            else:
                offset=0
            if offset>source.stat().st_size or shutil.disk_usage(directory).free<source.stat().st_size-offset+MARGIN:
                raise ValueError('Historical staging has an invalid checkpoint or insufficient local storage.')
            with source.open('rb') as src:
                if offset:
                    with partial.open('rb') as previous:
                        while block:=previous.read(8*1024**2):
                            if src.read(len(block))!=block:
                                raise ValueError('Historical staging checkpoint does not match.')
                with partial.open('ab' if partial.exists() else 'xb') as dst:
                    shutil.copyfileobj(src,dst,8*1024**2)
                    dst.flush()
                    os.fsync(dst.fileno())
            require_hash(partial,row['sha256'])
            partial.rename(path)
        staged[role]=row | {'path':str(path)}
    return staged


def packet_identity(w, path, stream):
    from fractions import Fraction
    result=json.loads(subprocess.check_output([str(w.exports.PROBE),'-v','error','-select_streams',stream,
        '-show_streams','-show_packets','-show_data_hash','SHA256','-show_entries',
        'stream=time_base:packet=pts,dts,duration,data_hash,side_data_list','-of','json',str(path)],timeout=600))
    base=Fraction(result['streams'][0]['time_base'])
    return [(int(p['pts'])*base,int(p['dts'])*base,int(p['duration'])*base,
             p['data_hash'],p.get('side_data_list',[])) for p in result['packets']]


def master_with_proxy_audio(w, files, proof):
    master=Path(files['Master']['path'])
    proxy=Path(files['Proxy']['path'])
    output=proof/'Master picture with proxy audio.mp4'
    if not output.exists():
        from wve_safety import MARGIN
        if shutil.disk_usage(proof).free<master.stat().st_size+proxy.stat().st_size+MARGIN:
            raise ValueError('Insufficient local space to preserve proxy audio.')
        subprocess.run([str(w.exports.FFMPEG),'-nostdin','-v','error','-xerror','-n','-i',str(master),
                        '-i',str(proxy),'-map','0:v:0','-map','1:a:0','-c','copy',
                        '-tag:v','hvc1',str(output)],check=True,timeout=600)
    for stream,source in (('v:0',master),('a:0',proxy)):
        if packet_identity(w,output,stream)!=packet_identity(w,source,stream):
            raise ValueError('The working media did not preserve the original packets.')
    return output,media_digest(output)


def prepare(panel, state, numbers, clear, *, just_exported=False):
    from capcut_ui import CapCut
    from desktop_controls import WinOcr
    rows=inventory(panel)
    w=engine(panel) if panel.live else None
    drafts=[]
    for n in numbers:
        key=str(n)
        if n not in rows or confirmed(panel,n) or state.get(key,{}).get('state') in ('submitted','returned','review','approved','publishing','exported','triggered'):
            continue
        if state.get(key,{}).get('state')=='prepared':
            continue
        if not clear:
            raise ValueError('Confirm these projects are absent from Space before preparing them.')
        row=rows[n]
        if w:
            d=w.exports.draft
            proxy=Path(row['files']['Proxy']['path'])
            require_hash(proxy,row['files']['Proxy']['sha256'])
            clip=d.probe(proxy,w.exports.PROBE,allow_aac_tail=True,external=True)
            if (clip['width'],clip['height'])!=(1280,720):
                raise ValueError('Expected the verified 720p proxy.')
            work=store(panel).parent/f'Prepare-{n}'
            work.mkdir(exist_ok=True)
            name=f'Vlog {n}'
            if (work/'prepared.json').is_file():
                draft=read(work/'prepared.json')
                if (w.exports.ROOT/name).is_dir():
                    _,registered=local_draft(w.exports.ROOT,name)
                    if registered['draft_id']!=draft['entry']['draft_id']:
                        raise ValueError('The existing editor draft requires reconciliation.')
                else:
                    drafts.append((work,draft))
            else:
                draft=d.prepare(w.exports.ROOT,proxy.parent,w.exports.PROBE,work,[],name,prepared_sources=[clip])
                drafts.append((work,draft))
            row['draft_id']=draft['entry']['draft_id']
        state[key]=row | {'state':'prepared','destination':settings(panel)}
    if drafts:
        w.verify_idle_renderer()
        with WinOcr() as ocr:
            ui=CapCut(w.exports,ocr)
            if ui.windows('CapCut'):
                if just_exported:
                    ui.navigate_home()
                ui.leave_home() # Stops if native work or an editor is open.
        w.exports.draft.publish_many(w.exports.ROOT,drafts,store(panel).parent)


def open_working(ui, root, name):
    from desktop_controls import Box, wait_for
    if not ui.windows('CapCut'):
        ui.launch()
    # Home can appear before the project list finishes populating.
    alternative=name.replace('(1)-copy','(l)-copy')
    def working_row():
        screen=ui.await_home()
        lines=[line for line in screen.lines if ' '.join(w['t'] for w in line).casefold() in (name.casefold(),alternative.casefold())]
        if len(lines)>1:
            raise ValueError('More than one working project matches. Open the exact draft manually.')
        if lines:
            word=lines[0][0]
            return screen,Box(word['x'],word['y'],word['w'],word['h'])
        return None
    screen,hit=wait_for(working_row,'one exact working-copy row',seconds=15)
    screen.click(ui.project_open_point(screen,hit),double=True)
    def opened():
        if not (root/name/'.locked').is_file():
            return None
        try:
            editor=ui.screen(ui.get_window('CapCut'))
        except RuntimeError as error:
            if 'lost the foreground' in str(error):
                return None
            raise
        return editor if ui.editor_for(editor,name) else None
    return wait_for(opened,'the exact master-backed editor',seconds=30)


def native_duplicate(w, name):
    from capcut_ui import CapCut
    from desktop_controls import WinOcr, user, wait_for
    root=w.exports.ROOT
    if (root/(name+'-copy')).exists():
        raise ValueError('An existing copy requires explicit selection and verification.')
    if list(root.glob('*/.locked')):
        raise ValueError('Close CapCut editors before duplicating.')
    with WinOcr() as ocr:
        ui=CapCut(w.exports,ocr)
        screen=ui.home() if ui.windows('CapCut') else ui.launch()
        # Require an exact text line to distinguish the returned draft from a copy.
        matches=[line for line in screen.lines if ' '.join(x['t'] for x in line)==name]
        if len(matches)!=1:
            raise ValueError('Cannot identify the exact returned project. Use CapCut Duplicate manually.')
        from desktop_controls import Box
        word=matches[0][0]
        x,y=ui.project_open_point(screen,Box(word['x'],word['y'],word['w'],word['h']))
        screen.check()
        if not screen.visible_at((x,y)):
            raise ValueError('The project row is obscured.')
        user.SetCursorPos(screen.bounds.x+x,screen.bounds.y+y)
        user.mouse_event(8,0,0,0,0)
        user.mouse_event(16,0,0,0,0)
        def menu():
            current=ui.screen(ui.get_window('CapCut'))
            return current if current.matches('Duplicate') and current.matches('Rename') else None
        context=wait_for(menu,'the exact local project menu')
        context.click(context.unique('Duplicate').center)
        wait_for(lambda:(root/(name+'-copy')).is_dir(),'the local CapCut duplicate')
        ui.leave_home()
    return name+'-copy'


def retrieve(panel,row,name,copy_name):
    w=engine(panel)
    root=w.exports.ROOT
    original,record=local_draft(root,name)
    if project_number(name)!=row['n'] or name.casefold().endswith('-copy'):
        raise ValueError('The returned project does not match the selected vlog.')
    if record.get('draft_cloud_last_action_download') is not True:
        raise ValueError('The local registry does not identify this project as a completed download.')
    if (original/'.locked').exists():
        raise ValueError('Close the returned project before preparing the working copy.')
    original_doc=read(original/'draft_content.json')
    source_proxy,videos=media_binding(original,original_doc,allow_missing=row.get('historical',False))
    if source_proxy.is_file():
        require_hash(source_proxy,row['files']['Proxy']['sha256'])
    proof=store(panel).parent/('Returned-'+record['draft_id'])
    files=staged_legacy_sources(row['files'],proof) if row.get('historical') else row['files']
    duration,needs_audio=validate_pair(w,files,historical=row.get('historical',False))
    if not source_proxy.is_file():
        if any((video.get('width'),video.get('height'),video.get('duration'),video.get('has_audio'))
               !=(1280,720,duration,True) for video in videos):
            raise ValueError('The missing proxy reference does not match the verified archive timing and profile.')
        ids={video['id'] for video in videos}
        spans=[segment['source_timerange'] for track in original_doc['tracks'] for segment in track['segments']
               if segment.get('material_id') in ids]
        if not spans or any(span['start']<0 or span['duration']<=0
           or span['start']+span['duration']>duration for span in spans):
            raise ValueError('Saved edits exceed the verified historical media timing.')
    w.verify_idle_renderer()
    copy_name=copy_name or native_duplicate(w,name)
    duplicate,copy_record=local_draft(root,copy_name)
    if (copy_name!=name+'-copy'
            or record['draft_id']==copy_record['draft_id'] or copy_record.get('cloud_draft_sync') is not False):
        raise ValueError('Use Keep both, then CapCut Duplicate, and select the exact local copy.')
    w.verify_idle_renderer(allow_capcut_previews=False)
    w.exports.draft.require_idle(root)
    original_revision=revision(original)
    if revision(duplicate)!=original_revision:
        raise ValueError('The selected copy does not have the same saved edits.')
    target,_=media_binding(duplicate,read(duplicate/'draft_content.json'),allow_missing=row.get('historical',False))
    if source_proxy.relative_to(original)!=target.relative_to(duplicate):
        raise ValueError('The duplicate has a different media binding.')
    if needs_audio:
        working_source,working_hash=master_with_proxy_audio(w,files,proof)
    else:
        working_source,working_hash=Path(files['Master']['path']),files['Master']['sha256']
    target_hash=media_digest(target) if target.is_file() else None
    if target_hash not in (None,row['files']['Proxy']['sha256'],working_hash):
        raise ValueError('Unexpected bundled media in the working copy.')
    snapshot={p.relative_to(original).as_posix():sealed(p) for p in original.rglob('*') if p.is_file()}
    backup=proof/'Untouched'
    if not backup.exists():
        shutil.copytree(original,backup)
    if snapshot!={p.relative_to(backup).as_posix():sealed(p) for p in backup.rglob('*') if p.is_file()}:
        raise ValueError('The original and preservation copy do not match.')
    if target_hash!=working_hash:
        temp=target.with_suffix('.master-partial')
        if not temp.exists():
            with temp.open('xb') as dst,working_source.open('rb') as source:
                shutil.copyfileobj(source,dst)
                dst.flush()
                os.fsync(dst.fileno())
        require_hash(temp,working_hash)
        if target_hash is None:
            if target.exists():
                raise ValueError('A new bundled file appeared during preparation.')
            temp.rename(target)  # Windows refuses to replace an existing path.
        else:
            require_hash(target,row['files']['Proxy']['sha256'])
            os.replace(temp,target)
    if revision(duplicate)!=original_revision:
        raise ValueError('The working timeline changed during substitution.')
    row.update(state='review',returned=name,working=copy_name,working_id=copy_record['draft_id'],revision=original_revision,working_media_sha256=working_hash)
    try:
        final_profile(original_doc)
    except ValueError as error:
        row['export_hold']=str(error)


def title(value):
    if not isinstance(value,str) or not re.fullmatch(r"[\w][\w .()&'-]{0,79}",value) or value.endswith(('.', ' ')):
        raise ValueError('Enter a valid title of up to 80 characters.')
    return value


def returned_matches(row, catalog):
    expected_cloud = row.get('cloud_entry_id')
    matches = []
    for entry in catalog:
        name = entry.get('draft_name','')
        if (entry.get('draft_cloud_last_action_download') is True
                and project_number(name)==row['n'] and not name.casefold().endswith('-copy')
                and (not expected_cloud or entry.get('tm_draft_cloud_entry_id') == expected_cloud)):
            matches.append(name)
    return sorted(set(matches))


def catalog(panel):
    if not panel.live:
        return [{'draft_name':f'Vlog {n} (1)','draft_cloud_last_action_download':True}
                for n in range(281,288)]
    w=engine(panel)
    return read(w.exports.ROOT/'root_meta_info.json',{}).get('all_draft_store',[])


def retrieval_selection(state, entries, picks):
    if not isinstance(picks,list) or not 1<=len(picks)<=20:
        raise ValueError('Select up to 20 returned projects.')
    jobs={}
    for pick in picks:
        n=pick['n']
        row=state.get(str(n),{})
        if type(n) is not int or row.get('state') not in RETURN_STATES or n in jobs:
            raise ValueError('Select an exact confirmed submission once.')
        matches=returned_matches(row,entries)
        name=pick.get('returned')
        if not name and len(matches)==1:
            name=matches[0]
        if name not in matches:
            raise ValueError(f'Choose a completed local return for Vlog {n}.')
        jobs[n]=name
    return jobs


def retrieve_job(panel, picks):
    path=store(panel)
    state=read(path,{})
    jobs=retrieval_selection(state,catalog(panel),picks)
    failures=[]
    if panel.live:
        w=engine(panel)
        lease=w.local_lock().open('a+b')
    else:
        lease=None
    try:
        if lease:
            lease.seek(0)
            w.msvcrt.locking(lease.fileno(),w.msvcrt.LK_NBLCK,1)
        for index,(n,name) in enumerate(jobs.items()):
            atomic(panel.state,{'status':'processing','operation':f'Preparing Vlog {n}',
                                 'editor_retrieval':True,'done':index,'total':len(jobs)})
            row=state[str(n)]
            try:
                if panel.live:
                    root=w.exports.ROOT
                    existing=name+'-copy'
                    retrieve(panel,row,name,existing if (root/existing).is_dir() else '')
                else:
                    row.update(state='review',returned=name,working=name+'-copy',revision='fixture-revision')
                row['master_verified']=True
                row.pop('retrieval_error',None)
            except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as error:
                from wve_safety import diagnostic
                row['retrieval_error']=diagnostic(error,None)['message']
                failures.append(n)
            atomic(path,state)
        atomic(panel.state,{'status':'error' if failures else 'complete',
                           'operation':'Preparing editor returns',
                           'message':f'{len(jobs)-len(failures)} of {len(jobs)} working copies ready.',
                           'editor_retrieval':True,'done':len(jobs),'total':len(jobs)})
    finally:
        if lease:
            lease.close()


def record_new_pairs(panel, numbers):
    """Only the current successfully published import can supply automatic eligibility."""
    path=store(panel)
    state=read(path,{})
    available=inventory(panel)
    for n in numbers:
        if n in available and str(n) not in state and not confirmed(panel,n):
            state[str(n)]=available[n] | {'state':'unshared'}
    atomic(path,state)
    return state


def eligible_returns(state, entries):
    return [{'n':row['n']} for row in state.values()
            if row['state'] in RETURN_STATES and not row.get('retrieval_error')
            and len(returned_matches(row,entries))==1]


def final_profile(doc):
    from fractions import Fraction
    raw=doc.get('fps')
    if isinstance(raw,(int,float)) and not isinstance(raw,bool):
        for exact,label in ((Fraction(30),'30fps'),(Fraction(30000,1001),'29.97fps')):
            if abs(raw-float(exact))<0.001:
                return exact,label
    raise ValueError('The saved timeline frame rate has not been qualified for final export.')


def verify_final(w, render, item):
    from fractions import Fraction
    data=w.exports.metadata(render,'Master',item['duration_us'])
    video=data['video']
    rate=Fraction(item['frame_rate'])
    if Fraction(video['avg_frame_rate'])!=rate:
        raise ValueError('The rendered frame rate differs from the approved timeline.')
    frames=round(Fraction(item['duration_us'],1_000_000)*rate)
    if int(video['nb_frames'])!=frames or abs(Fraction(video['duration'])-frames/rate)>Fraction(1,1_000_000):
        raise ValueError('The rendered frame count or duration differs from the approved timeline.')
    (v,video_packets),(a,audio_packets)=timing(w,render)
    if video_packets!=[(Fraction(n,1)/rate,1/rate) for n in range(frames)]:
        raise ValueError('The rendered video has a presentation timing gap or unexpected timestamp.')
    if (a.get('codec_name')!='aac' or not audio_packets or not data['audio']
        or abs(float(data['audio'][0]['duration'])-float(video['duration']))>.05
        or any(left[0]+left[1]!=right[0] for left,right in zip(audio_packets,audio_packets[1:]))):
        raise ValueError('The rendered editorial audio has a timing gap or mismatched duration.')
    subprocess.run([str(w.exports.FFMPEG),'-nostdin','-v','error','-xerror','-i',str(render),
                    '-map','0:v:0','-map','0:a:0','-f','null','-'],check=True,timeout=1800)
    return data


def approve_row(panel, path, state, row, offered):
    if row.get('export_hold') and row['export_hold']!='The historical timeline frame rate requires separate final export qualification.':
        raise ValueError(row['export_hold'])
    if (row.get('state') not in ('review','approved') or row.get('master_verified') is not True
            or offered.get('revision')!=row.get('revision')):
        raise ValueError('Review the current revision before approving.')
    if panel.live:
        w=engine(panel)
        draft,record=local_draft(w.exports.ROOT,row['working'])
        if (draft/'.locked').exists() or record['draft_id']!=row['working_id']:
            raise ValueError('Close the exact working copy after review.')
        final_profile(read(draft/'draft_content.json'))
        current=revision(draft)
        if current!=row['revision']:
            row.update(state='review',revision=current)
            atomic(path,state)
            raise ValueError('The draft changed. Reload and review the new revision before approving.')
    return title(offered['title'])


def action(panel,name,data):
    path=store(panel)
    state=read(path,{})
    if name=='editor-list':
        if any(r.get('state')=='exporting' for r in state.values()):
            return {'rows':list(state.values()),'space':settings(panel)}
        if read(panel.state,{}).get('status') in ('processing','interrupted'):
            return {'rows':list(state.values()),'space':settings(panel)}
        rows=inventory(panel)
        for n,row in rows.items():
            if confirmed(panel,n) and state.get(str(n),{}).get('state') in (None,'uncertain','prepared'):
                state[str(n)]=row | {'state':'submitted','destination':settings(panel)}
        entries=catalog(panel)
        discover_historical(panel,state,entries,rows)
        atomic(path,state)
        listed=[dict(state.get(str(n),row|{'state':'uncertain'})) for n,row in sorted(rows.items())]
        listed.extend(dict(row) for key,row in state.items() if int(key) not in rows)
        for row in listed:
            if (panel.live and row.get('export_hold')=='The historical timeline frame rate requires separate final export qualification.' and row.get('working')):
                w=engine(panel)
                try:
                    draft,_=local_draft(w.exports.ROOT,row['working'])
                    final_profile(read(draft/'draft_content.json'))
                    row.pop('export_hold',None)
                except (OSError,ValueError):
                    pass
            if row['state'] in RETURN_STATES:
                row['local_returns']=returned_matches(row,entries)
        return {'rows':listed,'space':settings(panel)}
    if name=='editor-sync':
        picks=eligible_returns(state,catalog(panel))
        if picks:
            panel.spawn('editor-retrieve-batch',{'picks':picks[:20]})
        return {'started':len(picks[:20])}
    if name=='editor-confirm-batch':
        numbers=data['numbers']
        if (data.get('confirmed') is not True or not isinstance(numbers,list)
                or not 1<=len(numbers)<=250 or any(type(n) is not int for n in numbers)
                or len(set(numbers))!=len(numbers)):
            raise ValueError('Confirm the completed uploads in this batch.')
        selected=[state.get(str(n),{}) for n in numbers]
        if any(row.get('state')!='prepared' for row in selected):
            raise ValueError('Reconcile any items that are not awaiting upload confirmation.')
        if any(row.get('destination')!=selected[0].get('destination') for row in selected):
            raise ValueError('Confirm one Space destination at a time.')
        stamp=datetime.now(timezone.utc).isoformat()
        for row in selected:
            row.update(state='submitted',confirmed_utc=stamp)
        atomic(path,state)
        return {'confirmed':len(selected)}
    if name=='editor-approve-export':
        if any(r.get('state') in ('triggered','restoring','publishing') for r in state.values()):
            raise ValueError('Reconcile the previous native export before submitting another batch.')
        picks=data['picks']
        if not isinstance(picks,list) or not 1<=len(picks)<=20 or data.get('confirmed') is not True:
            raise ValueError('Select up to 20 reviewed projects.')
        checked={}
        for pick in picks:
            n=pick['n']
            if type(n) is not int or n in checked:
                raise ValueError('Select each reviewed project once.')
            checked[n]=approve_row(panel,path,state,state.get(str(n),{}),pick)
        names=[value.casefold() for value in checked.values()]
        if len(names)!=len(set(names)):
            raise ValueError('Use a different final title for each selected vlog.')
        if panel.live:
            folder=panel.folders()['edited']
            if not folder.is_dir() or any((folder/(value+'.mp4')).exists() for value in checked.values()):
                raise ValueError('Edited Videos is unavailable or a final output already exists.')
        for n,value in checked.items():
            state[str(n)].update(state='approved' if panel.live else 'exported',title=value)
        atomic(path,state)
        if panel.live:
            panel.spawn('editor-export',{'numbers':list(checked)})
        return {'saved':True}
    if name=='editor-retrieve-batch':
        retrieval_selection(state,catalog(panel),data['picks'])
        return panel.spawn('editor-retrieve-batch',{'picks':data['picks']})
    if name=='editor-prepare':
        numbers=data['numbers']
        if not isinstance(numbers,list) or not numbers or len(numbers)>250 or any(type(n) is not int for n in numbers):
            raise ValueError('Select up to 250 verified projects.')
        if any(state.get(str(n),{}).get('state')!='unshared' for n in numbers):
            raise ValueError('Select only projects confirmed as unshared.')
        prepare(panel,state,sorted(set(numbers)),True)
    elif name=='editor-resume':
        if not panel.live:
            raise ValueError('There is no native publication in the visual fixture.')
        w=engine(panel)
        with w.local_lock().open('a+b') as lease:
            lease.seek(0)
            try:
                w.msvcrt.locking(lease.fileno(),w.msvcrt.LK_NBLCK,1)
            except OSError as error:
                raise ValueError('The existing controller still owns the native export.') from error
        numbers=[r['n'] for r in state.values() if r.get('state') in ('triggered','restoring','publishing')]
        if not numbers:
            raise ValueError('No saved native submission or verified publication checkpoint.')
        return panel.spawn('editor-export',{'numbers':numbers})
    elif name=='editor-export':
        if any(r.get('state') in ('exporting','triggered','restoring','publishing') for r in state.values()):
            raise ValueError('Reconcile the previous native export before submitting another batch.')
        numbers=data['numbers']
        if not isinstance(numbers,list) or not numbers or len(numbers)>20 or any(type(n) is not int for n in numbers):
            raise ValueError('Select up to 20 approved projects.')
        selected=[state.get(str(n),{}) for n in dict.fromkeys(numbers)]
        if any(row.get('state')!='approved' for row in selected):
            raise ValueError('Approve each current revision before exporting.')
        if not panel.live:
            for row in selected:
                row['state']='exported'
        else:
            return panel.spawn('editor-export',{'numbers':numbers})
    else:
        row=state.get(str(data['n']),{})
        if name=='editor-mark-unshared' and not row and data.get('checked_space') is True:
            number=data['n']
            rows=inventory(panel)
            if type(number) is not int or number not in rows or confirmed(panel,number):
                raise ValueError('No eligible verified pair for that vlog.')
            state[str(number)]=rows[number] | {'state':'unshared'}
            atomic(path,state)
            return {'saved':True}
        if name=='editor-open' and row.get('state') in ('review','approved'):
            if panel.live:
                w=engine(panel)
                from capcut_ui import CapCut
                from desktop_controls import WinOcr
                draft,entry=local_draft(w.exports.ROOT,row['working'])
                if entry['draft_id']!=row['working_id']:
                    raise ValueError('The exact working copy changed.')
                w.verify_idle_renderer()
                with WinOcr() as ocr:
                    ui=CapCut(w.exports,ocr)
                    locks=[p.parent.name for p in w.exports.ROOT.glob('*/.locked')]
                    if locks and locks!=[row['working']]:
                        raise ValueError('Close the currently open CapCut editor first.')
                    if not locks:
                        open_working(ui,w.exports.ROOT,row['working'])
                    elif not ui.editor_for(ui.screen(ui.get_window('CapCut')),row['working']):
                        raise ValueError('Cannot verify the open editor.')
            return {'opened':True}
        if name=='editor-submitted' and (not row or row.get('state')=='unshared') and data.get('confirmed') is True:
            number=data['n']
            if type(number) is not int or number not in (rows:=inventory(panel)):
                raise ValueError('No verified pair for the confirmed upload.')
            row=state[str(number)]=rows[number] | {'state':'prepared','destination':settings(panel)}
        if name=='editor-submitted' and row.get('state')=='prepared' and data.get('confirmed') is True:
            row.update(state='submitted',confirmed_utc=datetime.now(timezone.utc).isoformat())
        elif name=='editor-approve':
            value=approve_row(panel,path,state,row,data)
            row.update(state='approved',title=value)
        else:
            raise ValueError('Reconcile the current handoff state before proceeding.')
    atomic(path,state)
    return {'saved':True}

def final_job(panel, numbers):
    """Use CapCut's qualified local export primitives. Submit only once per revision."""
    from capcut_ui import CapCut
    from desktop_controls import Box, WinOcr, user, wait_for
    from wve_safety import publish_file
    w=engine(panel)
    path=store(panel)
    state=read(path,{})
    destination=panel.folders()['edited']
    destination_path=destination
    if not destination_path.is_dir():
        raise ValueError('Edited Videos is unavailable.')
    lease=w.local_lock()
    with lease.open('a+b') as lock:
        lock.seek(0)
        w.msvcrt.locking(lock.fileno(),w.msvcrt.LK_NBLCK,1)
        w.verify_idle_renderer()
        d=w.exports.draft
        for n in dict.fromkeys(numbers):
            row=state[str(n)]
            if row['state']=='exported':
                continue
            draft,record=local_draft(w.exports.ROOT,row['working'])
            if record['draft_id']!=row['working_id'] or revision(draft)!=row['revision']:
                raise ValueError(f'Vlog {n} changed after approval.')
            bundled,_=media_binding(draft,read(draft/'draft_content.json'),approved_audio=row.get('approved_external_audio'))
            require_hash(bundled,row.get('working_media_sha256',row['files']['Master']['sha256']))
            output=destination_path/(row['title']+'.mp4')
            w.exports.RUN=path.parent/f'Final-{n}-{row["revision"][:16]}'
            w.exports.RUN.mkdir(exist_ok=True)
            w.exports.PLAN=w.exports.RUN/'plan.json'
            w.exports.OUTPUT=w.exports.RUN
            (w.exports.OUTPUT/'Master').mkdir(exist_ok=True)
            doc=read(draft/'draft_content.json')
            rate,label=final_profile(doc)
            job_key=row.get('date') or f'historical-{n}'
            item=dict(title=row['working'],date=job_key,output_stem=f'Vlog {n}',duration_us=doc['duration'],
                      frame_rate=str(rate),frame_rate_label=label)
            if not w.exports.PLAN.exists():
                d.write_new(w.exports.PLAN,d.encode({'projects':[item]}))
            elif read(w.exports.PLAN).get('projects')!=[item]:
                raise ValueError('The saved export plan differs from the approved timeline.')
            render=w.exports.target(job_key,'Master')
            if row['state']=='approved':
                if output.exists() or render.exists() or list(w.exports.RUN.glob('*trigger*.json')):
                    raise ValueError(f'Existing final file or trigger held for Vlog {n}.')
                if shutil.disk_usage(w.exports.OUTPUT).free<w.reliability.MARGIN:
                    raise ValueError('Insufficient local render reserve.')
                with WinOcr() as ocr:
                    ui=CapCut(w.exports,ocr)
                    locks=[p.parent.name for p in w.exports.ROOT.glob('*/.locked')]
                    if locks and locks!=[row['working']]:
                        raise ValueError('Close other CapCut editors before the final export.')
                    if not locks:
                        open_working(ui,w.exports.ROOT,row['working'])
                    frame=ui.prepare_export(item,'Master',render)
                    area=Box(frame.bounds.w*2//3,frame.bounds.h-95,frame.bounds.w//3,95)
                    frame.button('Export',area)
                    if revision(draft)!=row['revision']:
                        ui.cancel_unsubmitted_setup(item)
                        raise ValueError('CapCut changed the approved timeline. Review again.')
                    row['state']='triggered'
                    atomic(path,state)
                    w.exports.confirm_export(item['date'],'Master')
                    ui.queued(frame,'Export-'+item['title'])
            if row['state']=='triggered':
                # The exact native result is required on restart. A file by itself
                # does not prove CapCut finished or that this controller produced it.
                receipt=w.exports.RUN/f'{job_key}-Master-trigger.json'
                if read(receipt,{}).get('path')!=str(render):
                    raise ValueError('Native submission was interrupted before a confirmed trigger. Review manually.')
                with WinOcr() as ocr:
                    ui=CapCut(w.exports,ocr)
                    expected='Export-'+item['title']
                    if not ui.windows(expected):
                        raise ValueError('The saved export has no matching native result. Review manually; no duplicate was submitted.')
                    def complete():
                        dialog=ui.dialog(expected)
                        return dialog if ui.finished(dialog) and render.is_file() else None
                    result=wait_for(complete,'the verified native export result',seconds=7200,poll=2)
                    size=render.stat().st_size
                    time.sleep(2)
                    if size<=0 or render.stat().st_size!=size:
                        raise ValueError('Native output size has not settled.')
                    verification=w.exports.RUN/'verified-final.json'
                    verified=read(verification,{})
                    if (verified.get('path')!=str(render) or verified.get('bytes')!=size
                            or verified.get('last_write')!=render.stat().st_mtime
                            or not verified.get('sha256')
                            or verified.get('video',{}).get('avg_frame_rate')!=str(rate.numerator)+'/'+str(rate.denominator)):
                        verified=verify_final(w,render,item)
                        stamp=w.exports.draft.source_stamp(render)
                        verified['sha256']=media_digest(render)
                        if stamp!=w.exports.draft.source_stamp(render):
                            raise ValueError('The verified render changed during fingerprinting.')
                        atomic(verification,verified)
                    else:
                        require_hash(render,verified['sha256'])
                    row.update(state='restoring',render_hash=verified['sha256'])
                    atomic(path,state)
            if row['state']=='restoring':
                require_hash(render,row['render_hash'])
                with WinOcr() as ocr:
                    ui=CapCut(w.exports,ocr)
                    expected='Export-'+item['title']
                    if ui.windows(expected):
                        result=ui.dialog(expected)
                        if ui.finished(result):
                            result.click(result.button('Close',Box(result.bounds.w//2,result.bounds.h-125,
                                                                     result.bounds.w//2,125)).center)
                    ui.restore_master(item)
                    editor=ui.screen(ui.get_window('CapCut'))
                    if not ui.editor_for(editor,item['title']):
                        raise RuntimeError('Cannot verify the completed editor before closing it.')
                    editor.check()
                    if not user.PostMessageW(editor.window,0x0010,0,0):
                        raise RuntimeError('CapCut declined to close the completed editor.')
                    wait_for(lambda: not (draft/'.locked').exists(),'the rendered editor to close')
                row.update(state='publishing',delivery={})
                atomic(path,state)
            if row['state']!='publishing':
                raise ValueError('Native submission is ambiguous. Reconcile it before retrying.')
            require_hash(render,row['render_hash'])
            publish_file(render,output,output.with_suffix('.vlogsync-partial'),row['delivery'],
                         lambda:atomic(path,state))
            row['state']='exported'
            atomic(path,state)
