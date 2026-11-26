"""Synthetic controller used by the isolated panel qualification."""
from datetime import date
from pathlib import Path
import time
import numbering
from wve_safety import atomic
from panel import read, sealed

DAYS=('2026-10-17','2026-10-18','2026-10-19')


def fixture_collides(panel, number):
    return any((panel.folders()[r]/f'Vlog {number}.mp4').exists() for r in ('master','proxy'))


def simulate(panel, action, data):
    current=read(panel.state,{})
    if action=='scan':
        n=numbering.status(numbering.read(panel.ledger))['next_number']
        projects=[dict(date=day,clips=count,external_count=extra,existing=existing,vlog_number=n+i,
                       identities=[f'fixture-{day}']) for i,(day,count,extra,existing) in
                  enumerate(zip(DAYS,(5,3,7),(0,1,0),({}, {'Master':True},{})))]
        atomic(panel.preview,dict(projects=projects,held=[]))
        atomic(panel.state,dict(status='review',review=sealed(panel.preview),existing_pairs=1,message='Review the three recording dates. One master already exists.'))
        return
    if action=='approve':
        assert sealed(panel.preview)==data['review']
        ledger=numbering.read(panel.ledger)
        reserved=numbering.bind(read(panel.preview)['projects'],ledger,reserve=True)
        if any(fixture_collides(panel,p['vlog_number']) for p in reserved):
            raise ValueError('A numbered output already exists.')
        atomic(panel.ledger,ledger)
        atomic(panel.root/'fixture-run.json',reserved)
        current.update(run='fixture-run',done=0,total=5,failed=0,position=0,completed=0,remaining=3)
    jobs=read(panel.root/'fixture-run.json')
    tasks=[(i,r) for r in ('Master','Proxy') for i in jobs if r not in i['existing']]
    atomic(panel.root/'control.json',{})
    for k in range(current['position'],len(tasks)):
        item,role=tasks[k]
        current.update(vlog=item['vlog_number'],role=role,operation=f'Creating {role.lower()}')
        atomic(panel.state,current)
        time.sleep(2)
        current.update(position=k+1,done=k+1,completed=max(0,k-1),remaining=3-max(0,k-1))
        request=read(panel.root/'control.json',{}).get('request')
        if request in ('pause','cancel'):
            current.update(status='paused' if request=='pause' else 'cancelled',
                           message='The current simulated export finished before the queue stopped.')
            atomic(panel.state,current)
            return
    current.update(status='complete',operation='Exports complete',completed=3,remaining=0,
                   message='Fixture run complete. No video files were created.')
    atomic(panel.state,current)
