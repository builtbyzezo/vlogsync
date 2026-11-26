"""Thin adapter around the existing Phase 1 commands."""
import hashlib
import json
import subprocess
import sys
from panel import ENGINE, CREATE, RUN, atomic, read, sealed


def command(panel, script, *args):
    result = subprocess.run([sys.executable,'-B',str(ENGINE/script),*args],
                            env=panel.env,text=True,capture_output=True,creationflags=CREATE)
    return result


def execute(panel, action, payload):
    import weekly_vlog_export as weekly
    state=read(panel.state,{})
    cli='weekly_vlog_export.py'
    if action=='scan':
        result=command(panel,cli,'--preview')
        if result.returncode:
            raise ValueError('Preview held. Check the camera, sources and destination and try again.')
        completed=sum('master and proxy; skipped' in line for line in result.stdout.splitlines())
        state.update(status='review',review=sealed(panel.preview),existing_pairs=completed,message='Review the dates before preparing exports.')
        atomic(panel.state,state)
        return
    if action=='approve':
        result=command(panel,cli,'--reserve-reviewed','--expect-review',payload['review'])
        if result.returncode:
            raise ValueError('The existing controller could not safely reserve the reviewed numbers. Scan again.')
        plan=read(panel.preview)
        key=hashlib.sha256(weekly.exports.draft.encode(weekly.reviewed_selection(plan))).hexdigest()[:16]
        run='Production-'+key
        digest=sealed(panel.preview)
    else:
        run=payload['run']
        digest=payload['review']
        if not RUN.fullmatch(run) or sealed(panel.preview)!=digest:
            raise ValueError('The frozen preview no longer matches the saved run.')
        if action=='retry':
            result=command(panel,'phase1_control.py','retry-failed',run,'--role',payload['role'],'--date',payload['date'])
            if result.returncode:
                raise ValueError('The existing controller declined this retry.')
        result=command(panel,'phase1_control.py','resume',run)
        if result.returncode:
            raise ValueError('The existing controller could not release the pause.')
    state.update(run=run,review=digest,operation='Preparing masters and proxies')
    atomic(panel.state,state)
    args=['--execute-preview','--expect-review',digest]
    if payload.get('power','none')!='none':
        args.extend(['--after-success',payload['power']])
    result=command(panel,cli,*args)
    if result.returncode==weekly.RESTART:
        result=command(panel,cli,*args)
    base=ENGINE/'Runs'/run
    flag=read(base/'control.json',{}).get('request')
    queue=read(base/'queue.json',{})
    if queue.get('publication_finished') and result.returncode==0:
        state.update(status='complete',operation='Exports complete',message='Local publication verified.')
    elif flag in ('pause','cancel') and result.returncode==76:
        state.update(status='paused' if flag=='pause' else 'cancelled',message='The controller stopped submitting work.')
    else:
        report=read(base/'troubleshooting.json',{})
        state.update(status='error',message=report.get('message','The run needs attention. Preserve the saved journal.'))
    atomic(panel.state,state)
