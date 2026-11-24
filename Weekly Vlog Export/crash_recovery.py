"""Bounded recovery for a positively identified lost native transaction."""
from pathlib import Path
import subprocess

from desktop_controls import pid,process_image,user,wait_for
import wve_safety as safety


def recover_verified_exit(weekly,ui,plan,state,journal,role,day,previous_pid):
    """Restart one job only after the original process and native work are gone.

    The caller must retain the acceptance record and the exact process identity.
    A missing file alone is insufficient evidence that CapCut abandoned a job.
    """
    if not previous_pid or process_image(previous_pid) is not None:
        raise RuntimeError('The former native renderer could still be running')
    processes=subprocess.run(['tasklist.exe','/FI',f'PID eq {previous_pid}','/FO','CSV','/NH'],
                             check=True,capture_output=True,text=True,timeout=12)
    if any(line.startswith('"') for line in processes.stdout.splitlines()):
        raise RuntimeError('The former renderer PID is still present or was reused')
    item,=[item for item in plan['projects'] if item['date']==day]
    job=state['rounds'][role].get(day,{})
    if job.get('state')!='queued' or job.get('native_pid')!=previous_pid or job.get('retry_count'):
        raise RuntimeError('The queued job does not carry a matching first-submission process identity')
    if sum(row['state'] in ('triggered','queued') for jobs in state['rounds'].values() for row in jobs.values())!=1:
        raise RuntimeError('Other native work requires reconciliation before an automatic retry')
    target=weekly.exports.target(day,role)
    if target.exists() or any(target.parent.iterdir()):
        raise RuntimeError('Existing final or partial files require review before recovery')
    locks=[path.parent.name for path in weekly.exports.ROOT.glob('*/.locked')]
    if locks and locks!=[item['title']]:
        raise RuntimeError('An unrelated project lock prevents recovery')
    def editor():
        handle=ui.current_window('CapCut')
        if not handle:
            return None
        try:
            frame=ui.screen(handle)
        except RuntimeError as error:
            if ('lost the foreground' in str(error)
                    and process_image(pid(user.GetForegroundWindow()))==ui.binary):
                return None
            raise
        return frame if ui.editor_for(frame,item['title']) else None
    if locks and editor():
        ui.navigate_home()
        locks=[]
    live=ui.await_home()
    if any(live.matches(text) for text in ('Waiting to export','Exporting')):
        raise RuntimeError('CapCut Home reports active native work')
    if locks:
        rows=ui.home_row(live,item['title'])
        if len(rows)!=1:
            raise RuntimeError('The exact stale project is not visible')
        live.click(rows[0].center,double=True)
        wait_for(editor,'the exact stale managed project',seconds=45)
        ui.navigate_home()
    ui.leave_home()
    weekly.exports.draft.require_idle(weekly.exports.ROOT)
    weekly.camera_check(plan)
    job['state']='failed'
    safety.safe_retry(state,role,day,Path(target.parent),target.name)
    weekly.save(journal,state)
    weekly.event(f'{role} {day}: one native retry reserved after verified renderer exit')
    ui.launch()
