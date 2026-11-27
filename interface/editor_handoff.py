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
    root = PHASE3/workspace_key if panel.live else panel.root
    root.mkdir(parents=True, exist_ok=True)
    return root/'editor-handoff.json'


def settings(panel):
    return DEFAULTS | read(panel.config,{}).get('space',{})


def engine(panel):
    import weekly_vlog_export as w
    os.environ['WEEKLY_VLOG_MANAGER_ROOT'] = str(panel.root)
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


def require_hash(file, expected):
    if sealed(file)!=expected:
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
        documents.append((path.relative_to(draft).as_posix(),doc))
    if not documents:
        raise ValueError('The draft has no readable timeline.')
    return hashlib.sha256(json.dumps(documents,sort_keys=True).encode()).hexdigest()


def local_draft(root, name):
    if not re.fullmatch(r'Vlog [0-9]+(?: \([0-9]+\))?(?:-copy)?',name):
        raise ValueError('Use an exact numbered CapCut project name.')
    row=[x for x in read(root/'root_meta_info.json',{})['all_draft_store'] if x['draft_name']==name]
    path=root/name
    if len(row)!=1 or Path(row[0]['draft_fold_path']).resolve()!=path.resolve() or not path.is_dir() or path.is_symlink():
        raise ValueError('The exact local project is unavailable or ambiguous.')
    return path,row[0]


def media_path(draft, doc):
    paths=set()
    for video in doc['materials']['videos']:
        match=re.fullmatch(r'##_draftpath_placeholder_[A-Fa-f0-9-]+_##/(materials/video/[^/\\]+\.mp4)',video['path'])
        if not match:
            raise ValueError('Unexpected video reference. Review this project manually.')
        paths.add(match[1])
    if len(paths)!=1:
        raise ValueError('The edit must reference exactly one bundled proxy.')
    file=draft/paths.pop()
    if not file.is_file() or file.resolve()!=file.absolute():
        raise ValueError('The bundled proxy is unavailable or redirected.')
    return file


def timing(w, path):
    from fractions import Fraction
    def timeline(stream):
        result=json.loads(subprocess.check_output([str(w.exports.PROBE),'-v','error','-select_streams',
            stream,'-show_streams','-show_packets','-show_entries','stream=codec_name,width,height,time_base,sample_rate,channels:packet=pts,duration',
            '-of','json',str(path)],timeout=180))
        info,=result['streams']
        base=Fraction(info['time_base'])
        return info,sorted((int(row['pts'])*base,int(row['duration'])*base) for row in result['packets'])
    return timeline('v:0'),timeline('a:0')


def validate_pair(w, files):
    for row in files.values():
        require_hash(Path(row['path']),row['sha256'])
    (m,mt),(a,at)=timing(w,Path(files['Master']['path']))
    (p,pt),(b,bt)=timing(w,Path(files['Proxy']['path']))
    if ((m.get('codec_name'), m.get('width'),m.get('height'))!=('hevc',1920,1080)
        or (p.get('codec_name'),p.get('width'),p.get('height'))!=('h264',1280,720)
        or mt!=pt or at!=bt or any(a[k]!=b[k] for k in ('codec_name','sample_rate','channels'))):
        raise ValueError('Master and proxy presentation timing or media profiles differ.')


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
    screen=ui.home() if ui.windows('CapCut') else ui.launch()
    # On this CapCut build OCR can display a numeric suffix 1 as lowercase l.
    alternative=name.replace('(1)-copy','(l)-copy')
    lines=[line for line in screen.lines if ' '.join(w['t'] for w in line) in (name,alternative)]
    if len(lines)!=1:
        raise ValueError('CapCut did not show one exact working copy. Open it manually for review.')
    word=lines[0][0]
    screen.click(ui.project_open_point(screen,Box(word['x'],word['y'],word['w'],word['h'])),double=True)
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
    expected_name=rf'Vlog {row["n"]} \([0-9]+\)'
    if not re.fullmatch(expected_name,name):
        raise ValueError('Select the exact returned project from Keep both.')
    if record.get('draft_cloud_last_action_download') is not True:
        raise ValueError('The local registry does not identify this project as a completed download.')
    if (original/'.locked').exists():
        raise ValueError('Close the returned project before preparing the working copy.')
    source_proxy=media_path(original,read(original/'draft_content.json'))
    require_hash(source_proxy,row['files']['Proxy']['sha256'])
    w.verify_idle_renderer()
    copy_name=copy_name or native_duplicate(w,name)
    duplicate,copy_record=local_draft(root,copy_name)
    if (copy_name!=name+'-copy'
            or record['draft_id']==copy_record['draft_id'] or copy_record.get('cloud_draft_sync') is not False):
        raise ValueError('Use Keep both, then CapCut Duplicate, and select the exact local copy.')
    w.verify_idle_renderer(allow_capcut_previews=False)
    w.exports.draft.require_idle(root)
    proof=store(panel).parent/('Returned-'+record['draft_id'])
    original_revision=revision(original)
    if revision(duplicate)!=original_revision:
        raise ValueError('The selected copy does not have the same saved edits.')
    src=media_path(original,read(original/'draft_content.json'))
    target=media_path(duplicate,read(duplicate/'draft_content.json'))
    if src.relative_to(original)!=target.relative_to(duplicate):
        raise ValueError('The duplicate has a different media binding.')
    require_hash(src,row['files']['Proxy']['sha256'])
    target_hash=sealed(target)
    if target_hash not in (row['files']['Proxy']['sha256'],row['files']['Master']['sha256']):
        raise ValueError('Unexpected bundled media in the working copy.')
    validate_pair(w,row['files'])
    snapshot={p.relative_to(original).as_posix():sealed(p) for p in original.rglob('*') if p.is_file()}
    backup=proof/'Untouched'
    if not backup.exists():
        shutil.copytree(original,backup)
    if snapshot!={p.relative_to(backup).as_posix():sealed(p) for p in backup.rglob('*') if p.is_file()}:
        raise ValueError('The original and preservation copy do not match.')
    if target_hash!=row['files']['Master']['sha256']:
        temp=target.with_suffix('.master-partial')
        if not temp.exists():
            with temp.open('xb') as dst,Path(row['files']['Master']['path']).open('rb') as source:
                shutil.copyfileobj(source,dst)
                dst.flush()
                os.fsync(dst.fileno())
        require_hash(temp,row['files']['Master']['sha256'])
        require_hash(target,row['files']['Proxy']['sha256'])
        os.replace(temp,target)
    if revision(duplicate)!=original_revision:
        raise ValueError('The working timeline changed during substitution.')
    row.update(state='review',returned=name,working=copy_name,working_id=copy_record['draft_id'],revision=original_revision)


def title(value):
    if not isinstance(value,str) or not re.fullmatch(r"[\w][\w .()&'-]{0,79}",value) or value.endswith(('.', ' ')):
        raise ValueError('Enter a valid title of up to 80 characters.')
    return value


def returned_matches(row, catalog):
    pattern = rf'Vlog {row["n"]} \([0-9]+\)'
    expected_cloud = row.get('cloud_entry_id')
    matches = []
    for entry in catalog:
        name = entry.get('draft_name','')
        if (entry.get('draft_cloud_last_action_download') is True
                and re.fullmatch(pattern,name)
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
        if type(n) is not int or row.get('state')!='submitted' or n in jobs:
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
        atomic(path,state)
        listed=[dict(state.get(str(n),row|{'state':'uncertain'})) for n,row in sorted(rows.items())]
        entries=catalog(panel)
        for row in listed:
            if row['state']=='submitted':
                row['local_returns']=returned_matches(row,entries)
        return {'rows':listed,'space':settings(panel)}
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
        numbers=[r['n'] for r in state.values() if r.get('state') in ('triggered','publishing')]
        if not numbers:
            raise ValueError('No saved native submission or verified publication checkpoint.')
        return panel.spawn('editor-export',{'numbers':numbers})
    elif name=='editor-export':
        if any(r.get('state') in ('exporting','triggered','publishing') for r in state.values()):
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
        elif name=='editor-approve' and row.get('state') in ('review','approved') and data.get('revision')==row.get('revision'):
            if panel.live:
                w=engine(panel)
                draft,record=local_draft(w.exports.ROOT,row['working'])
                if (draft/'.locked').exists() or record['draft_id']!=row['working_id']:
                    raise ValueError('Close the exact working copy after review.')
                current=revision(draft)
                if current!=row['revision']:
                    row.update(state='review',revision=current)
                    atomic(path,state)
                    raise ValueError('The draft changed. Reload and review the new revision before approving.')
            row.update(state='approved',title=title(data['title']))
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
            bundled=media_path(draft,read(draft/'draft_content.json'))
            require_hash(bundled,row['files']['Master']['sha256'])
            output=destination_path/(row['title']+'.mp4')
            w.exports.RUN=path.parent/f'Final-{n}-{row["revision"][:16]}'
            w.exports.RUN.mkdir(exist_ok=True)
            w.exports.PLAN=w.exports.RUN/'plan.json'
            w.exports.OUTPUT=w.exports.RUN
            (w.exports.OUTPUT/'Master').mkdir(exist_ok=True)
            doc=read(draft/'draft_content.json')
            item=dict(title=row['working'],date=row['date'],output_stem=f'Vlog {n}',duration_us=doc['duration'])
            if not w.exports.PLAN.exists():
                d.write_new(w.exports.PLAN,d.encode({'projects':[item]}))
            render=w.exports.target(row['date'],'Master')
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
                receipt=w.exports.RUN/f'{row["date"]}-Master-trigger.json'
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
                    w.output_info(render,'Master',{'duration_us':item['duration_us'],
                                  'sources':[{'has_audio':True}]})
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
                    row.update(state='publishing',render_hash=sealed(render),delivery={})
                    atomic(path,state)
            if row['state']!='publishing':
                raise ValueError('Native submission is ambiguous. Reconcile it before retrying.')
            require_hash(render,row['render_hash'])
            publish_file(render,output,output.with_suffix('.vlogsync-partial'),row['delivery'],
                         lambda:atomic(path,state))
            row['state']='exported'
            atomic(path,state)
