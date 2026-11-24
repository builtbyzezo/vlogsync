"""Prepare local CapCut drafts from original Insta360 MP4s without copying media."""
import argparse
import copy
from datetime import date
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
import uuid

NAME = 'AUTO TEST 1'
RATE = Fraction(30000, 1001)
UUID = re.compile(r'[a-f\d]{8}(?:-[a-f\d]{4}){3}-[a-f\d]{12}', re.I)


def uid():
    return str(uuid.uuid4()).upper()


def read(path):
    return json.loads(path.read_bytes())


def encode(doc):
    return json.dumps(doc, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def write_new(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def protected_snapshot(path):
    return {p.relative_to(path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in path.rglob('*') if p.is_file() and p.name != '.locked'}


def source_stamp(path):
    item = path.stat()
    return [item.st_size, item.st_mtime_ns]


def check_path(path):
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise RuntimeError(f'Linked path is outside the supported workflow: {path}')


def canonical_display_matrix(display, width, height):
    """Recognize the orthogonal, unscaled MP4 display transforms CapCut can import."""
    entries = tuple(int(value) for row in display.get('displaymatrix','').splitlines()
                    if ':' in row for value in row.split(':',1)[1].split())
    angle = display.get('rotation',0)%360
    one = 65536
    transforms = {0:(one,0,0,one,0,0), 90:(0,-one,one,0,0,width*one),
                  180:(-one,0,0,-one,width*one,height*one), 270:(0,one,-one,0,height*one,0)}
    if angle not in transforms:
        return False
    a,b,c,d,x,y = transforms[angle]
    return entries==(a,b,0,c,d,0,x,y,1073741824)


def probe(path, binary, *, allow_aac_tail=False, external=False, metadata=None,
          qualified_rotations=(), allow_camera_rotation=False):
    check_path(path)
    containers = ('.mp4','.mov','.m4v') if external else ('.mp4',)
    if not path.is_file() or path.suffix.lower() not in containers:
        raise RuntimeError(f'Unsupported or unavailable video container: {path}')
    before = source_stamp(path)
    doc = metadata if metadata is not None else json.loads(subprocess.run([str(binary), '-v', 'error', '-show_streams', '-show_format',
                                     '-of', 'json', str(path)], check=True, capture_output=True, timeout=20).stdout)
    if before != source_stamp(path):
        raise RuntimeError(f'Source changed during probe: {path}')
    video = [s for s in doc['streams'] if s['codec_type']=='video']
    audio = [s for s in doc['streams'] if s['codec_type']=='audio']
    if len(video)!=1 or len(audio)>1 or len(video)+len(audio)!=len(doc['streams']):
        raise RuntimeError('This small test supports one video and at most one original audio track.')
    video, = video
    display = next((s for s in video.get('side_data_list',[])
                    if s.get('side_data_type')=='Display Matrix'),{})
    rotation = display.get('rotation', 0)
    rate = Fraction(video['avg_frame_rate'])
    if (int(video['start_pts'])!=0 or video['width']*9!=video['height']*16
            or video.get('sample_aspect_ratio') not in (('1:1',None) if external else ('1:1',))
            or (not external and rate!=RATE)):
        raise RuntimeError(f'Clip has an unsupported timebase or aspect ratio: {path.name}')
    tag = int(video.get('tags',{}).get('rotate',rotation))
    if tag%360!=rotation%360 or (display and not canonical_display_matrix(display,video['width'],video['height'])):
        raise RuntimeError(f'Conflicting or non-orthogonal camera display metadata: {path.name}')
    if rotation and (external or video.get('codec_name')!='hevc'
                     or (video['width'],video['height'])!=(1920,1080)
                     or tuple(video.get(key) for key in ('color_space','color_transfer','color_primaries'))!=('bt709',)*3
                     or not (allow_camera_rotation or display.get('displaymatrix') in qualified_rotations)):
        raise RuntimeError(f'Camera display matrix has not been qualified: {path.name}')
    if external:
        if (video.get('codec_name') not in ('h264','hevc')
                or video.get('pix_fmt') not in ('yuv420p','yuvj420p')
                or video.get('color_transfer') in ('smpte2084','arib-std-b67')
                or video.get('color_primaries') not in (None,'unknown','bt709','bt470bg','smpte170m')
                or rate not in {Fraction(n,d) for n,d in ((24000,1001),(24,1),(25,1),(30000,1001),
                                                              (30,1),(50,1),(60000,1001),(60,1))}
                or Fraction(video['r_frame_rate'])!=rate
                or any(int(s.get('channels',0)) not in (1,2)
                       or not (s.get('codec_name')=='aac' or s.get('codec_name','').startswith('pcm_'))
                       for s in audio)):
            raise RuntimeError(f'External footage requires format review (codec, color, frame rate or audio): {path.name}')
    exact = int(video['duration_ts'])*Fraction(video['time_base'])
    duration = round(exact*1_000_000)
    container = Fraction(doc['format']['duration'])
    if abs(duration-round(container*1_000_000))>1:
        track = audio[0] if audio else {}
        if not (allow_aac_tail and track.get('codec_name')=='aac' and int(track['start_pts'])==0
                # Preserve an exactly timed, zero-start AAC tail, up to two frames.
                and 0 < container-exact <= max(2/rate,1/rate + Fraction(1024,int(track['sample_rate'])))
                and abs(container-int(track['duration_ts'])*Fraction(track['time_base']))<=Fraction(1,1_000_000)):
            raise RuntimeError(f'Camera container and picture lengths differ: {path.name}')
        duration = round(container*1_000_000)
    clip = dict(path=path.as_posix(), name=path.name, duration=duration,
                frames=int(video['nb_frames']), width=video['width'], height=video['height'],
                has_audio=bool(audio), rotation=0, stamp=before)
    if rotation:
        # CapCut interprets the source display matrix. An editor rotation would apply a second turn.
        clip['source_rotation'] = rotation
    return clip


def remap(obj, ids):
    if isinstance(obj, dict):
        return {key:remap(value,ids) for key,value in obj.items()}
    if isinstance(obj, list):
        return [remap(item,ids) for item in obj]
    if isinstance(obj,str) and UUID.fullmatch(obj):
        if obj not in ids:
            ids[obj]=uid()
        return ids[obj]
    return obj


def prepare(root, camera, binary, work, names, title, *, keep_order=False, allow_aac_tail=False,
            prepared_sources=None):
    blank, populated = root/'1123', root/'1122'
    base = read(blank/'draft_content.json')
    model = read(populated/'draft_content.json')
    project = read(blank/'Timelines/project.json')
    mini_blank = read(blank/'Timelines'/project['main_timeline_id']/'attachment/patch/mini_draft.json')
    mini_model = read(populated/'Timelines'/model['id']/'attachment/patch/mini_draft.json')
    meta = read(blank/'draft_meta_info.json')
    meta_model = read(populated/'draft_meta_info.json')
    meta_material = next(item for item in meta_model['draft_materials'][0]['value'] if item['metetype']=='video')
    virtual = read(populated/'draft_virtual_store.json')
    catalog = read(root/'root_meta_info.json')
    if base['tracks'] or base['new_version']!='193.0.0' or len(model['tracks'])!=1 or len(model['tracks'][0]['segments'])!=1:
        raise RuntimeError('Local CapCut template structure differs from the inspected version.')
    entry = next(item for item in catalog['all_draft_store'] if item['draft_id']==meta['draft_id'])
    if any(item['draft_name'].casefold()==title.casefold() for item in catalog['all_draft_store']) or (root/title).exists():
        raise FileExistsError(title)

    if prepared_sources is None:
        names = list(names) if keep_order else sorted(names)
        if (not names or len(set(names))!=len(names)
                or any(not re.fullmatch(r'VID_\d{8}_\d{6}_\d+\.mp4',n) for n in names)):
            raise RuntimeError('Supply distinct timestamped Insta360 MP4 filenames.')
        clips=[probe(camera/name,binary,allow_aac_tail=allow_aac_tail) for name in names]
    else:
        clips=list(prepared_sources)
        paths=[Path(s['path']) for s in clips]
        if not paths or len(set(paths))!=len(paths):
            raise RuntimeError('Prepared sources are empty or duplicate a path')
        for clip,path in zip(clips,paths,strict=True):
            check_path(path)
            if not path.is_absolute() or source_stamp(path)!=clip['stamp']:
                raise RuntimeError(f'A prepared source changed: {path}')
    total=sum(clip['duration'] for clip in clips)
    now=time.time_ns()//1000
    draft_id, timeline_id, project_id, track_id = uid(),uid(),uid(),uid()
    destination=root/title
    folder=destination.as_posix()
    track=copy.deepcopy(model['tracks'][0])
    segment_ref=track['segments'][0]
    video_ref=model['materials']['videos'][0]
    track.update(id=track_id,segments=[])
    mini_segments=[]
    bin_rows=[]
    maps=[]
    cursor=0
    for index,clip in enumerate(clips):
        ids={}
        video=remap(video_ref,ids)
        segment=remap(segment_ref,ids)
        for key,values in model['materials'].items():
            for value in values:
                if value.get('id') in segment_ref['extra_material_refs']:
                    base['materials'][key].append(remap(value,ids))
        fields={key:clip[key] for key in ('path','duration','width','height','has_audio')}
        video.update(fields)
        video['material_name']=clip['name']
        segment.update(source_timerange=dict(start=0,duration=clip['duration']),
                       target_timerange=dict(start=cursor,duration=clip['duration']))
        base['materials']['videos'].append(video)
        track['segments'].append(segment)
        mini=remap(mini_model['mini_draft_data']['segments'][0],ids)
        mini['material'].update(fields)
        mini['source_time_range'].update(segment['source_timerange'])
        mini['target_time_range'].update(segment['target_timerange'])
        mini_segments.append(mini)
        bin_rows.append(dict(meta_material,id=uid(),file_Path=clip['path'],extra_info=clip['name'],
                             duration=clip['duration'],width=clip['width'],height=clip['height'],
                             roughcut_time_range=segment['source_timerange']))
        maps.append(dict(short_id=str(1000+index),uuid=segment['id']))
        cursor+=clip['duration']

    base.update(id=timeline_id,duration=total,fps=float(RATE),tracks=[track],color_space=0)
    meta.update(draft_id=draft_id,draft_name=title,draft_fold_path=folder,
                draft_root_path=str(root),tm_draft_create=now,tm_draft_modified=now,tm_duration=total,
                draft_timeline_materials_size_=sum(c['stamp'][0] for c in clips))
    meta['draft_materials'][0]['value']=bin_rows
    virtual['draft_virtual_store'][1]['value']=[dict(child_id=item['id'],parent_id='') for item in bin_rows]
    project.update(id=project_id,main_timeline_id=timeline_id,create_time=now,update_time=now)
    project['timelines'][0].update(id=timeline_id,create_time=now,update_time=now)
    layout=read(blank/'timeline_layout.json')
    layout['dockItems'][0]['timelineIds']=[timeline_id]
    mini=mini_blank['mini_draft_data']
    mini['draft'].update(id=timeline_id,duration=total,fps=float(RATE),color_space=0,
                         tracks=[dict(attribute=track['attribute'],id=track_id,segments=[s['id'] for s in track['segments']])])
    mini['header'].update(draft_id=timeline_id,timestamp=now//1_000_000,timestamp_ms=now//1000)
    mini['segments']=mini_segments
    mapping={'id_mapping':{'mapping':maps,'next_index':1000+len(maps),'version':'1.0.0'}}
    entry=dict(entry, draft_id=draft_id,draft_fold_path=folder,draft_name=title,draft_root_path=str(root),
               draft_cover=str(destination/'draft_cover.jpg'),draft_json_file=str(destination/'draft_content.json'),
               tm_draft_create=now,tm_draft_modified=now,tm_duration=total,
               draft_timeline_materials_size=meta['draft_timeline_materials_size_'])
    build=work/'draft'
    build.mkdir()
    timeline=build/'Timelines'/timeline_id
    for path,doc in [
        (build/'draft_content.json',base),(timeline/'draft_content.json',base),
        (build/'draft_meta_info.json',meta),(build/'draft_virtual_store.json',virtual),
        (build/'timeline_layout.json',layout),(build/'Timelines/project.json',project),
        (build/'Timelines/project.json.bak',project),(build/'key_value.json',{}),
        (timeline/'attachment/patch/mini_draft.json',mini_blank),
        (timeline/'attachment/patch/patch.json',{'patch_data':[]}),
        (build/'common_attachment/attachment_id_mapping.json',mapping),
        (timeline/'common_attachment/attachment_id_mapping.json',mapping),
        (build/'draft_agency_config.json',read(blank/'draft_agency_config.json')),
    ]:
        write_new(path,encode(doc))
    write_new(build/'draft_settings',f'[General]\ndraft_create_time={now//1000000}\ndraft_last_edit_time={now//1000000}\nreal_edit_seconds=0\nreal_edit_keys=1\n'.encode())
    state=dict(entry=entry,title=title,clips=clips,blank=protected_snapshot(blank),
               populated=protected_snapshot(populated))
    verify(build,state)
    write_new(work/'prepared.json',encode(state))
    return state


def verify(project_dir,state):
    content=read(project_dir/'draft_content.json')
    meta=read(project_dir/'draft_meta_info.json')
    registry=read(project_dir/'Timelines/project.json')
    layout=read(project_dir/'timeline_layout.json')
    timeline=registry['main_timeline_id']
    assert content['id']==timeline==registry['timelines'][0]['id']==layout['dockItems'][0]['timelineIds'][0]
    assert read(project_dir/'Timelines'/timeline/'draft_content.json')==content
    track,=content['tracks']
    assert track['type']=='video' and not content['config']['video_mute']
    resources={item['id']:item for rows in content['materials'].values() for item in rows}
    assert len(resources)==sum(len(rows) for rows in content['materials'].values())
    assert len(content['materials']['videos'])==len(track['segments'])==len(state['clips'])>0
    timeline_dir=project_dir/'Timelines'/timeline
    mini=read(timeline_dir/'attachment/patch/mini_draft.json')['mini_draft_data']
    ids_in_order=[item['id'] for item in track['segments']]
    assert mini['draft']['id']==mini['header']['draft_id']==timeline
    assert mini['draft']['tracks'][0]['segments']==ids_in_order
    assert [item['id'] for item in mini['segments']]==ids_in_order
    assert [item['material']['path'] for item in mini['segments']]==[clip['path'] for clip in state['clips']]
    position=0
    for segment,clip,bin_item in zip(track['segments'],state['clips'],meta['draft_materials'][0]['value'],strict=True):
        video=resources[segment['material_id']]
        assert all(video[key]==clip[key] for key in ('path','duration','width','height','has_audio'))
        assert bin_item['file_Path']==clip['path'] and Path(clip['path']).is_file()
        assert source_stamp(Path(clip['path']))==clip['stamp']
        span=clip['duration']
        assert segment['source_timerange']==dict(start=0,duration=span)
        assert segment['target_timerange']==dict(start=position,duration=span)
        assert segment['speed']==1 and segment['volume']==1 and segment['clip']['rotation']==clip['rotation']
        assert all(ref in resources for ref in segment['extra_material_refs'])
        position+=span
    assert position==content['duration']==meta['tm_duration']==state['entry']['tm_duration']
    ids=[meta['draft_id'],registry['id'],timeline,track['id'],*resources,*[s['id'] for s in track['segments']]]
    assert len(ids)==len(set(ids)) and all(UUID.fullmatch(i) for i in ids)
    assert all(p.suffix.lower() not in ('.mp4','.mov','.m4a','.aac') for p in project_dir.rglob('*'))
    return set(ids)


def require_idle(root):
    result=subprocess.run(['tasklist.exe','/FI','IMAGENAME eq CapCut.exe','/FO','CSV','/NH'],
                          capture_output=True,text=True,check=True,timeout=12)
    if '"capcut.exe"' in result.stdout.casefold():
        raise RuntimeError('CapCut is running. Exit normally before registering the prepared draft.')
    if any(path.is_file() for path in root.glob('*/.locked')):
        raise RuntimeError('CapCut project lock still present. Open and close CapCut normally to clear it.')


def publish(root,work,state):
    publish_many(root,[(work,state)],work)


def publish_many(root,drafts,registration_work,protected=None):
    require_idle(root)
    protected=dict(protected or {})
    moves=[]
    used_ids=set()
    used_names=set()
    for work,state in drafts:
        build=work/'draft'
        destination=root/state['title']
        if destination!=Path(state['entry']['draft_fold_path']) or destination.exists():
            raise FileExistsError(f'The intended draft destination is unavailable: {destination}')
        title=state['title'].casefold()
        ids=verify(build,state)
        if used_ids & ids or title in used_names or (work/'published.json').exists():
            raise RuntimeError('Prepared drafts have conflicting identifiers or are already registered.')
        used_ids.update(ids)
        used_names.add(title)
        for name,key in [('1123','blank'),('1122','populated')]:
            if name in protected and protected[name]!=state[key]:
                raise RuntimeError('CapCut templates were edited between preparations.')
            protected[name]=state[key]
        moves.append((build,destination))
    for name,before in protected.items():
        if protected_snapshot(root/name)!=before:
            raise RuntimeError(f'CapCut project {name} changed; prepare a fresh run.')
    index=root/'root_meta_info.json'
    old=index.read_bytes()
    document=json.loads(old)
    entries=[state['entry'] for _,state in drafts]
    destinations={dest for _,dest in moves}
    for row in document['all_draft_store']:
        if (row['draft_id'] in used_ids or row['draft_name'].casefold() in used_names
                or Path(row['draft_fold_path']) in destinations):
            raise FileExistsError('Draft name, path or ID already registered.')
    document['all_draft_store'][0:0]=entries
    document['draft_ids']+=len(entries)
    suffix=uuid.uuid4().hex
    write_new(registration_work/f'root_meta_info-before-{suffix}.json',old)
    pending=registration_work/f'catalog-{suffix}.json'
    write_new(pending,encode(document))
    require_idle(root)
    moved=[]
    try:
        for build,destination in moves:
            os.rename(build,destination)  # Windows refuses to replace an existing folder.
            moved.append((build,destination))
        require_idle(root)
        if index.read_bytes()!=old:
            raise RuntimeError('CapCut catalog changed during registration. Existing entries were preserved.')
        os.replace(pending,index)
    except Exception:
        require_idle(root)
        for build,destination in reversed(moved):
            os.rename(destination,build)
        raise
    saved=read(index)
    before=json.loads(old)
    assert saved['all_draft_store']==entries+before['all_draft_store']
    assert saved['draft_ids']==before['draft_ids']+len(entries)
    assert all(protected_snapshot(root/name)==snapshot for name,snapshot in protected.items())
    for (work,state),(_,destination) in zip(drafts,moves,strict=True):
        verify(destination,state)
        write_new(work/'published.json',encode(dict(path=str(destination),clips=state['clips'],
                            duration_us=state['entry']['tm_duration'],preserved_existing_entries=len(before['all_draft_store']))))
        print(f'Registered {state["title"]}: {destination}')
