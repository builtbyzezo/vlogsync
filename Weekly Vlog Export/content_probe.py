"""Independent sampled comparison. A discrepancy calls for review."""
import array
from pathlib import Path
import subprocess

from wve_safety import matches_sample


def read(binary,path,seconds,video):
    arguments = ['-map','0:v:0','-frames:v','1','-vf',
                 'scale=64:36:force_original_aspect_ratio=decrease,pad=64:36:(ow-iw)/2:(oh-ih)/2,format=gray',
                 '-f','rawvideo'] if video else ['-map','0:a:0?','-vn','-t','0.4','-ac','1','-ar','8000','-f','s16le']
    result = subprocess.run([str(binary),'-nostdin','-v','error','-xerror','-ss',str(seconds),'-i',
                             str(path),*arguments,'pipe:1'],capture_output=True,check=False,timeout=30)
    if result.returncode:
        if not video and b"does not contain any stream" in result.stderr:
            return b''
        raise RuntimeError('Could not decode the selected content sample: '+Path(path).name)
    return result.stdout


def level(data):
    samples = array.array('h',data)
    return (sum(n*n for n in samples)/max(len(samples),1))**.5


def compare(binary,source,output,start,duration):
    mismatches = []
    for fraction in (.20,.53,.82):
        at = fraction*duration
        expected = read(binary,source,at,True)
        actual = read(binary,output,start+at,True)
        if not matches_sample(expected,actual):
            mismatches.append(dict(time=at,reason='Rendered picture differs from the source sample'))
        audio = level(read(binary,source,at,False))
        delivered = level(read(binary,output,start+at,False))
        if audio>200 and delivered<audio/12:
            mismatches.append(dict(time=at,reason='Expected audible source is absent in the rendered sample'))
    return mismatches


def compare_pair(weekly,item,state):
    day = item['date']
    records = {role:state['rounds'][role][day] for role in weekly.exports.ROUND}
    paths = {role:Path(result['metadata']['path']) for role,result in records.items()}
    expected = {role:result['signature'] for role,result in records.items()}
    stamps = weekly.exports.draft.source_stamp
    if any(stamps(paths[role])!=value for role,value in expected.items()):
        raise RuntimeError('One or more output files changed before content comparison')
    existing = state.get('content_evidence',{}).get(day,{})
    if existing.get('signatures')==expected and existing.get('identities')==item['identities']:
        return
    binary = weekly.exports.FFMPEG
    cursor = 0.0
    for source,identity in zip(item['sources'],item['identities'],strict=True):
        path = Path(source['path'])
        if stamps(path)!=source['stamp'] or weekly.camera.sampled_id(path)!=identity:
            raise RuntimeError(f'Camera source changed: {path.name}')
        duration = source['duration']/1e6
        for role,output in paths.items():
            failures = compare(binary,path,output,cursor,duration)
            if failures:
                raise RuntimeError(f'{role} content needs review for {path.name} at {failures[0]["time"]:.2f}s '
                                   f'from the clip start. {failures[0]["reason"]}')
        cursor += duration
    if any(stamps(paths[role])!=value for role,value in expected.items()):
        raise RuntimeError('An output changed during content comparison')
    state.setdefault('content_evidence',{})[day] = dict(signatures=expected,identities=item['identities'],
                                                       samples_per_clip_per_output=3)
