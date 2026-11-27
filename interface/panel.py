"""Loopback-only VlogSync operator panel. The default workspace is disposable."""
import argparse
from datetime import date
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import msvcrt
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser

HERE = Path(__file__).resolve().parent
ENGINE = HERE.parent / 'Weekly Vlog Export'
sys.path.insert(0, str(ENGINE))
import numbering
from wve_safety import atomic, diagnostic

VERSION = '1.3.0-rc1'
ROLES = ('Master', 'Proxy')
NAMES = {'extra':'Extra Footage','master':'1080p Master Vlogs',
         'proxy':'720p Editor Proxies','edited':'Edited Videos'}
RUN = re.compile(r'Production-[a-f0-9]{16}')
CREATE = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP


def read(path, default=None):
    try:
        return json.loads(path.read_bytes())
    except FileNotFoundError:
        return default


def sealed(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Panel:
    def __init__(self, root, live, native_pilot=False):
        self.root, self.live, self.native_pilot = root.resolve(), live or native_pilot, native_pilot
        live=self.live
        self.state = self.root / 'panel-state.json'
        self.config = self.root / 'panel-settings.json'
        self.ledger = (self.root / 'Configuration' if live else self.root) / 'numbering.json'
        self.preview = ENGINE / 'production-preview.json' if live else self.root / 'preview.json'
        self.env = dict(os.environ, WEEKLY_VLOG_MANAGER_ROOT=str(self.root))
        self.env.pop('VLOGSYNC_DISPOSABLE_NATIVE',None)
        if native_pilot:
            self.env['VLOGSYNC_DISPOSABLE_NATIVE']=str(self.root)
            from panel_fixture import allowed
            if not allowed(self.root):
                raise ValueError('The native pilot requires its exact disposable workspace.')
            self.preview = self.root/'production-preview.json'
        self.runs = self.root/'Runs' if native_pilot else ENGINE/'Runs'
        self.mutex = threading.Lock()
        self.child = None
        if live:
            # An unqualified candidate cannot be activated by typing a workspace path alone.
            approved = read(self.root / 'Configuration' / 'phase2-qualification.json', {})
            if approved != {'approved_version': VERSION}:
                raise ValueError('This workspace has not been approved for the Phase 2 candidate.')
            self.guard()
        else:
            self.root.mkdir(parents=True, exist_ok=True)
            if not self.ledger.exists():
                editor_preview = self.root == HERE/'Runs'/'Phase 3 Visual Review'
                atomic(self.ledger, {'schema':1,'observed_through':0,'last_assigned':
                       {'number':281 if editor_preview else 32,
                        'recording_date':'2026-11-25' if editor_preview else '2026-10-16'},'assignments':[]})
            for name in NAMES.values():
                (self.root/name).mkdir(exist_ok=True)

    def guard(self):
        if self.live and not self.native_pilot and (Path(os.environ['LOCALAPPDATA'])/'VlogSync/drive-organization-in-progress').exists():
            raise ValueError('The Phase 1 recovery hold is still active.')

    def folders(self):
        cfg = read(self.root/'Configuration/controller.json', {}) if self.live else {}
        paths = cfg.get('filesystem_exports',{})
        names = dict(NAMES,extra=cfg.get('extra_footage', NAMES['extra']),
                     master=paths.get('Master',NAMES['master']),proxy=paths.get('Proxy',NAMES['proxy']),
                     edited=cfg.get('edited_folder',NAMES['edited']))
        from deployment import under
        return {key:under(self.root.parent if self.live else self.root, value) for key,value in names.items()}

    def status(self):
        s = read(self.state, {'status':'ready'})
        if s['status'] in ('scanning','processing') and self.child is not None and self.child.poll() is not None:
            s = read(self.state,s)
            if s['status'] in ('scanning','processing'):
                s.update(status='interrupted',message='Check the saved operation before submitting again.')
                atomic(self.state,s)
        # After a server restart the worker writes the same file. Never guess that it has stopped.
        if self.live:
            from camera_media import detect_camera_folders
            connected = bool(list(detect_camera_folders()))
        else:
            connected = read(self.config,{}).get('camera_connected',True)
        settings = read(self.config,{})
        folders = self.folders()
        p = read(self.preview,{}) if s['status']=='review' else {}
        projects = [{k:i.get(k) for k in ('date','clips','vlog_number','external_count')}
                    | {'missing':[r for r in ROLES if r not in i.get('existing',{})]} for i in p.get('projects',[])]
        current = numbering.status(numbering.read(self.ledger))
        run = s.get('run')
        if self.live and isinstance(run,str) and RUN.fullmatch(run):
            queue = read(self.runs/run/'queue.json',{})
            plan = read(self.runs/run/'plan.json',{})
            s.update(journal_progress(plan,queue))
        issue = None
        if s['status'] in ('error','interrupted'):
            issue = dict(controller=VERSION,operation=s.get('operation','Import Vlogs'),
                         status=s['status'],message=s.get('message','The controller stopped.'),
                         recovery='Preserve the frozen preview, journal and outputs. Review the native queue before retrying.',
                         run=s.get('run'))
            if self.live and isinstance(run,str) and RUN.fullmatch(run):
                report = read(self.runs/run/'troubleshooting.json',{})
                issue.update({key:report[key] for key in
                              ('operation','vlog_numbers','journal_exists','export_states','capcut_windows')
                              if key in report})
        return dict(version=VERSION, demo=not self.live,camera=connected,activity=s,numbering=current,
                    projects=projects, held=[i[0] for i in p.get('held',[])],
                    review=s.get('review'),settings=settings,
                    native_pilot=self.native_pilot, folders={key:{'path':str(path),'available':path.is_dir()} for key,path in folders.items()},
                    free_gib=round(shutil.disk_usage(self.root).free/1024**3),
                    issue=issue)

    def action(self, name, data):
        with self.mutex:
            self.guard()
            status = self.status()
            activity = status['activity']
            mode = activity['status']
            busy = mode in ('scanning','processing','interrupted')
            if name.startswith('editor-'):
                own_child = self.child is not None and self.child.poll() is None
                editor_recovery = activity.get('editor_operation') and name in ('editor-list','editor-resume')
                if own_child or (busy and not editor_recovery):
                    raise ValueError('Wait for the active operation and reconcile any interruption.')
                from editor_handoff import action
                return action(self,name,data)
            if name=='folder':
                path = self.folders()[data['key']]
                if not path.is_dir():
                    raise ValueError('The selected destination is unavailable.')
                os.startfile(path)
                return
            if name=='diagnostic' and status['issue']:
                report = self.root/'VlogSync diagnostic.json'
                atomic(report,status['issue'])
                os.startfile(report)
                return
            if name=='clearance' and not busy:
                if self.live and not self.native_pilot:
                    result = subprocess.run([sys.executable,'-B',str(ENGINE/'phase1_control.py'),'camera-assessment'],
                                            env=self.env,capture_output=True,text=True,timeout=600,creationflags=CREATE)
                    if result.returncode==0:
                        return json.loads(result.stdout)
                return {'safe_to_disconnect':False,'safe_to_format':False,
                        'issues':['No current qualified recording coverage is available. Keep the originals.']}
            if name=='settings' and not busy:
                power = data['power']
                if power not in ('none','sleep','shutdown') or type(data['auto_open']) is not bool:
                    raise ValueError('Invalid completion behavior.')
                editor_settings = {}
                if 'space' in data:
                    space, auto_prepare = data['space'],data.get('auto_prepare')
                    if (not isinstance(space,dict) or set(space)!= {'space','upload','completed'}
                            or any(not isinstance(x,str) or not 1<=len(x.strip())<=80 for x in space.values())
                            or type(auto_prepare) is not bool):
                        raise ValueError('Enter a Space and both destination folder names.')
                    editor_settings={'space':{k:v.strip() for k,v in space.items()},'auto_prepare':auto_prepare}
                atomic(self.config, {**status['settings'],'power':power,'auto_open':data['auto_open'],**editor_settings})
                return
            if name=='number' and (mode in ('ready','complete') or mode=='error' and not activity.get('run')):
                num = data['number']
                recorded = date.fromisoformat(data['date']).isoformat()
                if type(num) is not int or num<0 or data.get('confirm') is not True:
                    raise ValueError('Confirm the corrected last number and recording date.')
                if self.live:
                    self.cli('--set-last-number',str(num),recorded)
                else:
                    ledger=numbering.read(self.ledger)
                    from simulated_controller import fixture_collides
                    if fixture_collides(self,num+1):
                        raise ValueError('The next number conflicts with an existing output.')
                    numbering.edit_anchor(ledger,num,recorded)
                    atomic(self.ledger,ledger)
                return
            if name in ('pause','cancel') and mode=='processing':
                if not activity.get('run'):
                    raise ValueError('Wait for the controller to identify its run.')
                self.control(name,activity['run'])
                return
            if name=='scan' and (mode in ('ready','review','complete') or mode=='error' and not activity.get('run')):
                return self.spawn('scan',{})
            if name=='approve' and mode=='review' and status['projects']:
                if data.get('review')!=status['review'] or sealed(self.preview)!=status['review']:
                    raise ValueError('The preview changed. Scan again before approving.')
                if any(not r['available'] for k,r in status['folders'].items() if k in ('master','proxy')):
                    raise ValueError('A publication destination is unavailable.')
                power = status['settings'].get('power','none')
                if self.live and not self.native_pilot and power!='none' and data.get('confirm_power')!=power:
                    raise ValueError('Confirm the power action for this run.')
                return self.spawn('approve',{'review':status['review'],'power':'none' if self.native_pilot else power})
            if name in ('resume','retry') and (mode=='paused' or name=='retry' and mode=='error') and activity.get('run'):
                return self.spawn(name,dict(run=activity['run'],role=data.get('role'),date=data.get('date'),
                                            review=activity.get('review'),power='none'))
            raise ValueError('That action is unavailable in the current operation state.')

    def cli(self,*args):
        result=subprocess.run([sys.executable,'-B',str(ENGINE/'weekly_vlog_export.py'),*args],
                              env=self.env,capture_output=True,text=True,creationflags=CREATE)
        if result.returncode:
            raise ValueError('The existing controller declined the request. Review the run or check numbering conflicts.')

    def control(self, action, run):
        if self.live:
            if not RUN.fullmatch(run):
                raise ValueError('Invalid controller run.')
            subprocess.run([sys.executable,'-B',str(ENGINE/'phase1_control.py'),action,run],
                           env=self.env,check=True,creationflags=CREATE)
        else:
            atomic(self.root/'control.json',{'request':action})

    def spawn(self, action, payload):
        if self.child is not None and self.child.poll() is None:
            raise ValueError('The previous controller is finishing. Try again in a moment.')
        previous=read(self.state,{'status':'ready'})
        atomic(self.state,{**previous,**payload,'status':'scanning' if action=='scan' else 'processing',
                           'operation':'Scanning recordings' if action=='scan' else 'Preparing exports',
                           'message':'','run':payload.get('run'),'editor_operation':action=='editor-export'})
        try:
            self.child=subprocess.Popen([sys.executable,'-B',str(Path(__file__)), '--workspace',str(self.root),
                                     *(['--native-pilot'] if self.native_pilot else ['--live'] if self.live else []),'--worker',action,json.dumps(payload)],
                                     cwd=HERE,creationflags=CREATE)
        except OSError:
            atomic(self.state,previous)
            raise


def journal_progress(plan, queue):
    rounds=queue.get('rounds',{})
    projects=plan.get('projects',[])
    count=sum(rounds.get(r,{}).get(i['date'],{}).get('state')=='complete' for i in projects for r in ROLES)
    failed=[{'role':r,'date':i['date']} for i in projects for r in ROLES
            if rounds.get(r,{}).get(i['date'],{}).get('state')=='failed']
    active=[(i,r) for i in projects for r in ROLES
            if rounds.get(r,{}).get(i['date'],{}).get('state') in ('triggered','queued')]
    i,r=active[0] if active else ({},None)
    delivery=queue.get('delivery',{})
    pairs=sum(all(delivery.get(r,{}).get(i['date'],{}).get('transaction',{}).get('state')=='verified'
                  for r in ROLES) for i in projects)
    return dict(done=count,total=2*len(projects),completed=pairs,remaining=len(projects)-pairs,
                failed=len(failed),retryable=failed,vlog=i.get('vlog_number'),role=r)


def worker(panel, action, data):
    from simulated_controller import simulate
    try:
        if not panel.live:
            return simulate(panel,action,data)
        if action=='editor-export':
            from editor_handoff import final_job
            final_job(panel,data['numbers'])
            atomic(panel.state,{'status':'complete','operation':'Edited videos delivered'})
            return
        from phase2_bridge import execute
        execute(panel,action,data)
        saved=read(panel.state,{})
        if (action=='approve' and saved.get('status')=='complete'
                and read(panel.config,{}).get('auto_prepare') is True and not panel.native_pilot):
            from editor_handoff import prepare, store
            path=store(panel)
            state=read(path,{})
            plan=read(panel.runs/saved['run']/'plan.json',{})
            numbers=[p['vlog_number'] for p in plan.get('projects',[]) if not p.get('existing')]
            try:
                if numbers:
                    prepare(panel,state,numbers,True,just_exported=True)
                    atomic(path,state)
                    saved['message']='Local publication verified. Editor projects prepared for manual transfer.'
                    atomic(panel.state,saved)
            except (OSError,ValueError,RuntimeError) as error:
                saved['message']='Import complete. Editor preparation held: '+diagnostic(error,saved.get('run'))['message']
                atomic(panel.state,saved)
    except Exception as e:
        saved=read(panel.state,{})
        saved.update(status='error',message=diagnostic(e,saved.get('run'))['message'])
        atomic(panel.state,saved)


def handler(panel, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            return

        def send(self,status,body,kind):
            self.send_response(status)
            self.send_header('Content-Type',kind)
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def reply(self,status,value):
            self.send(status,json.dumps(value).encode(),'application/json; charset=utf-8')

        def allowed(self):
            return self.headers.get('Host')==f'127.0.0.1:{self.server.server_port}'

        def do_GET(self):
            if not self.allowed():
                return self.reply(403,{'error':'Invalid local address.'})
            if self.path=='/api/state':
                if not secrets.compare_digest(self.headers.get('X-VlogSync',''),token):
                    return self.reply(403,{'error':'Invalid panel session.'})
                try:
                    with panel.mutex:
                        return self.reply(200,panel.status())
                except (OSError,ValueError,RuntimeError):
                    return self.reply(409,{'error':'The workspace or saved operation is unavailable.'})
            files={'/':('index.html','text/html'),'/app.css':('app.css','text/css'),'/app.js':('app.js','text/javascript')}
            if self.path not in files:
                return self.reply(404,{'error':'Unknown path.'})
            filename,kind=files[self.path]
            self.send(200,(HERE/filename).read_bytes(),kind+'; charset=utf-8')

        def do_POST(self):
            if (not self.allowed() or self.headers.get('Origin')!=f'http://127.0.0.1:{self.server.server_port}'
                or not secrets.compare_digest(self.headers.get('X-VlogSync',''),token)):
                length=self.headers.get('Content-Length','')
                if length.isdecimal() and 0<int(length)<=4096:
                    self.connection.settimeout(2)
                    try:
                        self.rfile.read(int(length))
                    except (OSError,TimeoutError):
                        return
                return self.reply(403,{'error':'This request did not come from VlogSync.'})
            try:
                length=int(self.headers.get('Content-Length','0'))
                if self.headers.get_content_type()!='application/json' or not 1<=length<=4096:
                    raise ValueError('Invalid request.')
                self.connection.settimeout(5)
                body=json.loads(self.rfile.read(length))
                if not isinstance(body,dict):
                    raise ValueError('Invalid request.')
                result=panel.action(self.path.removeprefix('/api/'),body)
                self.reply(200,{'result':result})
            except (KeyError,TypeError,ValueError,OSError,RuntimeError,subprocess.SubprocessError) as error:
                self.reply(409,{'error':diagnostic(error,None)['message']})
    return Handler


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--workspace',type=Path)
    p.add_argument('--live',action='store_true')
    p.add_argument('--native-pilot',action='store_true')
    p.add_argument('--no-browser',action='store_true')
    p.add_argument('--watch-camera',action='store_true')
    p.add_argument('--worker',nargs=2,help=argparse.SUPPRESS)
    a=p.parse_args()
    if a.workspace is None:
        if a.live:
            from deployment import locate
            a.workspace, _ = locate()
        else:
            a.workspace = HERE/'Runs'/'Disposable panel'
    if not a.live and not a.workspace.resolve().is_relative_to(HERE/'Runs'):
        p.error('Simulated workspaces must stay under interface/Runs.')
    panel=Panel(a.workspace,a.live,a.native_pilot)
    if a.worker:
        return worker(panel,a.worker[0],json.loads(a.worker[1]))
    lock=panel.root/'panel.lock'
    with lock.open('a+b') as f:
        f.seek(0)
        try:
            msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:
            raise SystemExit('This workspace already has a VlogSync panel open.')
        token=secrets.token_urlsafe(32)
        with ThreadingHTTPServer(('127.0.0.1',0),handler(panel,token)) as server:
            url=f'http://127.0.0.1:{server.server_port}/#{token}'
            if a.watch_camera:
                def watch():
                    was_connected=False
                    while True:
                        try:
                            connected=panel.status()['camera']
                            if connected and not was_connected and read(panel.config,{}).get('auto_open',False):
                                webbrowser.open(url)
                            was_connected=connected
                        except (ValueError,OSError,RuntimeError):
                            pass
                        time.sleep(3)
                threading.Thread(target=watch,daemon=True).start()
            print(url,flush=True)
            if not a.no_browser:
                webbrowser.open(url)
            server.serve_forever()


if __name__=='__main__':
    main()
