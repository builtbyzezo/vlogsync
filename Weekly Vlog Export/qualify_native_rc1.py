"""Disposable two-project queue, pause, resume, cancel and preset qualification."""
import json
from pathlib import Path
import subprocess
import sys
import time

import wve_safety as reliability
import weekly_vlog_export as w


NAME='Test RC1 Recovery'
DAYS=('2026-01-03','2026-01-04')
TITLE=('WVE RC One','WVE RC Two')


def main():
    w.verify_idle_renderer(allow_capcut_previews=False,qualification_readers=True)
    reopen='--resume-prepared' in sys.argv
    if not reopen:
        w.exports.draft.require_idle(w.exports.ROOT)
    scaled='--100-percent' in sys.argv
    if scaled:
        from desktop_controls import user
        if user.GetDpiForSystem()!=96:
            raise RuntimeError('Display scale must be 100 percent for this qualification')
    root=w.HOME/'Runs'/(NAME+(' 100 percent' if scaled else ''))
    root.mkdir(parents=True,exist_ok=reopen)
    camera=root/'Disposable sources'
    camera.mkdir(exist_ok=reopen)
    w.exports.RUN=root
    w.exports.PLAN=root/'plan.json'
    w.exports.OUTPUT=root/'Disposable exports'
    tool=w.exports.PROBE.with_name('ffmpeg.exe')
    items=[]
    for i,(day,title) in enumerate(zip(DAYS,('WVE Scale One','WVE Scale Two') if scaled else TITLE,strict=True)):
        name=f'VID_{day.replace("-","")}_090000_{100+i}.mp4'
        path=camera/name
        cmd=[str(tool),'-v','error','-nostdin','-f','lavfi','-i',
             'testsrc2=s=1920x1080:r=30000/1001','-f','lavfi','-i',f'sine=frequency={400+i*100}',
             '-t','18','-c:v','libx264','-preset','ultrafast','-c:a','aac',str(path)]
        if not reopen:
            subprocess.run(cmd,check=True,timeout=90)
        source=w.exports.draft.probe(path,w.exports.PROBE,allow_aac_tail=True)
        items.append(dict(date=day,title=title,sources=[source],duration_us=source['duration']))
    plan=dict(camera=str(camera),projects=items,mode='weekly',output=str(w.exports.OUTPUT),disposable_fixture=True)
    if reopen:
        if w.exports.draft.read(w.exports.PLAN)!=plan or list(root.glob('*trigger*.json')):
            raise RuntimeError('Only a verified pre-submission fixture can be reopened')
    else:
        reliability.atomic(w.exports.PLAN,plan)
    for role in w.exports.ROUND:
        (w.exports.OUTPUT/role).mkdir(parents=True,exist_ok=reopen)
    journal=root/'queue.json'
    control=root/'control.json'
    state=dict(rounds={role:{} for role in w.exports.ROUND},max_outstanding={role:0 for role in w.exports.ROUND})
    started=time.monotonic()
    with reliability.Awake(),w.WinOcr() as o:
        ui=w.CapCut(w.exports,o)
        w.register(plan,ui)
        owned_setup = (reopen and
                       [p.parent.name for p in w.exports.ROOT.glob('*/.locked')]==[items[0]['title']]
                       and ui.current_window('Export-'+items[0]['title']))
        if not owned_setup:
            home=ui.await_home() if ui.windows('CapCut') else ui.launch()
            if scaled:
                user.ShowWindow(home.window,3)
                ui.await_home()
        normal=ui.return_home
        def pause_when_accepted(screen):
            reliability.atomic(control,dict(request='pause'))
            return normal(screen)
        ui.return_home=pause_when_accepted
        assert w.queue_round(plan,ui,state,journal,'Master',False)==76
        assert set(state['rounds']['Master'])=={DAYS[0]}
        verified=state['rounds']['Master'][DAYS[0]]
        assert verified['state']=='complete'
        print('PASS pause stopped submissions and drained the first native job',flush=True)
        ui.return_home=normal
        reliability.atomic(control,dict(request='cancel'))
        assert w.queue_round(plan,ui,state,journal,'Master',False)==76
        assert len(list(root.glob('*Master-trigger.json')))==1
        print('PASS cancel suppressed further submissions and preserved the completed master',flush=True)
        reliability.atomic(control,dict(request='resume'))
        saved=w.exports.draft.read(journal)
        assert saved['rounds']['Master'][DAYS[0]]==verified
        assert w.queue_round(plan,ui,saved,journal,'Master',False)==0
        assert w.queue_round(plan,ui,saved,journal,'Proxy',False)==0
        w.pairs(plan,saved)
        assert len(list(root.glob('*trigger.json')))==4
        ui.restore_master(items[0])
        saved['preset_restored']=True
        reliability.atomic(journal,saved)
        ui.navigate_home()
        ui.leave_home()
    print('PASS resume reused the first master, finished four renders, and restored 1080p HEVC',round(time.monotonic()-started,1),flush=True)


if __name__=='__main__':
    main()
