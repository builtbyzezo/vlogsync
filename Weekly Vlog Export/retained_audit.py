"""Read-only ordered-source spot verification of the completed 28-pair archive."""
from array import array
from datetime import datetime, timezone
from pathlib import Path
import argparse
from deployment import historical_run
import audioop
import math
import shutil
import subprocess

import content_probe as content
import wve_safety as reliability
import weekly_vlog_export as w


def audio_match(source, rendered):
    if not source or content.level(source)<100:
        return None  # An inaudible sample supplies no positive audio evidence.
    if not rendered or content.level(rendered)<max(35,content.level(source)/15):
        return False
    # Compare the inner source waveform to the expected output window. Allow
    # packet/seek rounding within 80 ms, while rejecting unrelated audible audio.
    middle=source[1280:-1280]
    if not middle or len(rendered)<len(middle):
        return False
    offset,factor=audioop.findfit(rendered,middle)
    matching=rendered[2*offset:2*offset+len(middle)]
    a,b=array('h',middle),array('h',matching)
    den=math.sqrt(sum(x*x for x in a)*sum(y*y for y in b))
    return bool(den and abs(offset-640)<=640 and sum(x*y for x,y in zip(a,b))/den>=.60)


def audio_followup(binary,source,output,local_time,output_time):
    """Check a longer source waveform against a bounded output window."""
    def read_audio(path,at,length):
        args=[str(binary),'-v','error','-nostdin','-ss',str(at),'-i',str(path),'-map','0:a:0',
              '-t',str(length),'-ac','1','-ar','8000','-f','s16le','pipe:1']
        return subprocess.run(args,capture_output=True,check=True,timeout=30).stdout
    pad=min(.4,output_time)
    original=read_audio(source,local_time,.60)
    rendered=read_audio(output,output_time-pad,.60+2*pad)
    if not original or len(rendered)<len(original):
        return dict(passed=False)
    offset,_=audioop.findfit(rendered,original)
    matching=rendered[2*offset:2*offset+len(original)]
    a,b=array('h',original),array('h',matching)
    norm=math.sqrt(sum(x*x for x in a)*sum(y*y for y in b))
    corr=sum(x*y for x,y in zip(a,b))/norm if norm else 0
    shift=offset/8000-pad
    return dict(passed=corr>=.80 and abs(shift)<=.08,correlation=round(corr,4),shift_ms=round(shift*1000,2))


def picture_followup(binary,source,output,at,position):
    """Check both sides of a possible one-frame seek-boundary discrepancy."""
    a=content.read(binary,source,at,True)
    b=content.read(binary,output,position,True)
    def distance(u,v):
        return sum(abs(x-y) for x,y in zip(u,v))/len(u) if u and len(u)==len(v) else 255.0
    measured=[]
    for direction in (1,-1,2,-2):
        shift=direction*1001/30000
        if min(at-shift,position+shift)<0:
            continue
        forward=distance(a,content.read(binary,output,position+shift,True))
        reverse=distance(b,content.read(binary,source,at-shift,True))
        measured.append(dict(frames=direction,sample=round(forward,3),reciprocal=round(reverse,3)))
        if max(forward,reverse)<=8:
            return dict(passed=True,checks=measured)
    return dict(passed=False,checks=measured)


def sample(binary,source,outputs,offset,duration):
    evidence={role:[] for role in outputs}
    for f in (.22,.51,.79):
        at=duration*f
        src_picture=content.read(binary,source,at,True)
        src_audio=content.read(binary,source,at,False)
        for role,path in outputs.items():
            rendered=content.read(binary,path,offset+at,True)
            if not src_picture or len(rendered)!=len(src_picture):
                raise RuntimeError('No complete video sample')
            difference=sum(abs(x-y) for x,y in zip(src_picture,rendered))/len(rendered)
            rendered_audio=content.read(binary,path,offset+at,False)
            evidence[role].append(dict(seconds=round(at,6),picture_mae=round(difference,4),
                                       picture_match=difference<=12,audio_match=audio_match(src_audio,rendered_audio)))
    return evidence


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--local-only',action='store_true')
    parser.add_argument('--only')
    parser.add_argument('--followup',action='store_true',help='Recheck flagged audio with a longer sample; preserve all earlier evidence.')
    args=parser.parse_args()
    w.configure_production()
    manager=w.MANAGER_ROOT/'Records'
    folder=manager/'Runs'/historical_run(w.MANAGER_ROOT)
    plan=w.exports.draft.read(folder/'plan.json')
    queue=w.exports.draft.read(folder/'queue.json')
    final={r['date']:r for r in w.exports.draft.read(folder/'final-verification.json')['dates']}
    archived={(r['date'],r['role']):r for r in w.exports.draft.read(manager/'keep-reconciliation.json')['rows']}
    baseline={r['date']:r for r in w.exports.draft.read(w.CAMERA_AUDIT)['groups']}
    numbered={r['date']:r for r in plan['projects']}
    camera=w.camera_path()
    target=w.HOME/'Retained pair content audit.json'
    report=w.camera.read_json(target,{'groups':{},'policy':'three relative luminance and audio waveform samples per source per role'})
    groups=w.exports.draft.read(w.exports.REVIEW)['groups']
    for group in sorted(groups,key=lambda row:(row['vlog_date'] not in numbered,row['vlog_date'])):
        day=group['vlog_date']; current=numbered.get(day)
        if (args.local_only and not current) or (args.only and args.only!=day):
            continue
        if [c['identity'] for c in group['clips']]!=baseline[day]['ordered_identities']:
            raise RuntimeError(f'Review order differs from the completed audit for {day}')
        if current and [c['identity'] for c in group['clips']]!=current['identities']:
            raise RuntimeError(f'Render order differs from the completed audit for {day}')
        outputs={}
        for role in w.exports.ROUND:
            if current:
                record=queue['rounds'][role][day]
                path=Path(record['metadata']['path'])
                expected=record['signature']
            else:
                path=w.publication_path(archived[day,role]['keep_path'])
                expected=baseline[day]['recognized'][role]['signature']
            if w.exports.draft.source_stamp(path)!=expected or expected[0]!=final[day]['roles'][role]['size']:
                raise RuntimeError(f'Retained file identity changed for {day} {role}')
            outputs[role]=path
        signatures={role:w.exports.draft.source_stamp(path) for role,path in outputs.items()}
        previous=report['groups'].get(day,{})
        if previous and previous['signatures']!=signatures:
            raise RuntimeError(f'A retained file changed after comparison for {day}')
        row=report['groups'].setdefault(day,dict(signatures=signatures,clips={}))
        cursor=0.0
        for index,clip in enumerate(group['clips']):
            duration=current['sources'][index]['duration']/1e6 if current else float(clip['duration'])
            path=camera/Path(clip['path']).name
            if not camera.is_dir() or not path.is_file() or w.camera.sampled_id(path)!=clip['identity']:
                raise RuntimeError(f'Source absent or changed: {path.name}')
            previous_clip=row['clips'].get(path.name)
            if args.followup and previous_clip and not previous_clip['passed'] and previous_clip.get('roles'):
                for role,samples in previous_clip['roles'].items():
                    for comparison in samples:
                        at=comparison['seconds']
                        if not comparison['picture_match'] and 'picture_followup' not in comparison:
                            try:
                                comparison['picture_followup']=picture_followup(w.exports.PROBE.with_name('ffmpeg.exe'),
                                                                 path,outputs[role],at,cursor+at)
                                master=next((p for p in previous_clip['roles'].get('Master',[])
                                             if p['seconds']==at),{})
                                if (role=='Proxy' and master.get('picture_match')
                                        and not comparison['picture_followup']['passed']):
                                    probe=w.exports.PROBE.with_name('ffmpeg.exe')
                                    a=content.read(probe,outputs['Master'],cursor+at,True)
                                    b=content.read(probe,outputs['Proxy'],cursor+at,True)
                                    if a and len(a)==len(b):
                                        delta=sum(abs(x-y) for x,y in zip(a,b))/len(a)
                                        comparison['picture_followup']['master_proxy_mae']=round(delta,4)
                                        comparison['picture_followup']['passed']=delta<=4
                            except (OSError,RuntimeError,subprocess.SubprocessError) as error:
                                comparison['picture_followup']=dict(passed=False,error=str(error))
                        if comparison['audio_match'] is False and 'audio_followup' not in comparison:
                            try:
                                comparison['audio_followup']=audio_followup(w.exports.PROBE.with_name('ffmpeg.exe'),
                                                                 path,outputs[role],at,cursor+at)
                            except (OSError,RuntimeError,subprocess.SubprocessError) as error:
                                comparison['audio_followup']=dict(passed=False,error=str(error))
                previous_clip['passed']=all(
                    (a['picture_match'] or a.get('picture_followup',{}).get('passed'))
                    and (a['audio_match'] is not False or a.get('audio_followup',{}).get('passed'))
                    for samples in previous_clip['roles'].values() for a in samples)
                reliability.atomic(target,report)
            if not args.followup and path.name not in row['clips']:
                if shutil.disk_usage(Path.home()).free < (40 if not current else 12)*1024**3:
                    reliability.atomic(target,report)
                    raise RuntimeError('Audit stopped at the disk reserve. Earlier samples preserved.')
                try:
                    comparisons=sample(w.exports.PROBE.with_name('ffmpeg.exe'),path,outputs,cursor,duration)
                    passed=all(all(p['picture_match'] and p['audio_match'] is not False for p in samples)
                               for samples in comparisons.values())
                    row['clips'][path.name]=dict(identity=clip['identity'],offset_seconds=cursor,
                                                duration_seconds=duration,passed=passed,roles=comparisons)
                except (OSError,RuntimeError,subprocess.SubprocessError) as error:
                    row['clips'][path.name]=dict(identity=clip['identity'],passed=False,error=str(error))
                reliability.atomic(target,report)
            cursor+=duration
        row['checked']=datetime.now(timezone.utc).isoformat()
        reliability.atomic(target,report)
        print(day,len(row['clips']),'clips',sum(not x['passed'] for x in row['clips'].values()),'need review',flush=True)


if __name__=='__main__':
    main()
