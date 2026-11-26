from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from panel import Panel, handler


class PanelTests(unittest.TestCase):
    def setUp(self):
        (Path(__file__).parent / "Runs").mkdir(exist_ok=True)
        self.workspace = TemporaryDirectory(dir=Path(__file__).parent / "Runs")
        self.addCleanup(self.workspace.cleanup)
        self.panel = Panel(Path(self.workspace.name), False)
        self.addCleanup(self.join_worker)
        self.server = ThreadingHTTPServer(('127.0.0.1',0),handler(self.panel,'fixture-key'))
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url='http://127.0.0.1:'+str(self.server.server_port)

    def join_worker(self):
        if self.panel.child is not None:
            self.panel.child.wait(timeout=12)

    def call(self, action, body=None, token='fixture-key', origin=None):
        payload=None if body is None else json.dumps(body).encode()
        request=urllib.request.Request(self.url+'/api/'+action,data=payload,
            headers={'X-VlogSync':token,'Content-Type':'application/json','Origin':origin or self.url})
        try:
            with urllib.request.urlopen(request,timeout=4) as result:
                return result.status,json.load(result)
        except urllib.error.HTTPError as error:
            with error:
                return error.code,json.load(error)

    def wait(self, status, limit=12):
        until=time.monotonic()+limit
        while time.monotonic()<until:
            code,s=self.call('state')
            if code==200 and s['activity']['status']==status:
                return s
            time.sleep(.12)
        self.fail(f'Panel never reached {status}: {s}')

    def test_api_rejects_cross_origin_and_unsanctioned_requests(self):
        self.assertEqual(self.call('scan',{},origin='https://example.test')[0],403)
        self.assertEqual(self.call('scan',{},token='incorrect')[0],403)
        self.assertEqual(self.call('state',token='incorrect')[0],403)
        self.assertEqual(self.call('state')[1]['activity']['status'],'ready')

    def test_review_duplicate_pause_resume_and_reopen(self):
        self.assertEqual(self.call('scan',{})[0],200)
        preview=self.wait('review')
        self.assertEqual(len(preview['projects']),3)
        self.assertEqual(preview['activity']['existing_pairs'],1)
        self.join_worker()
        self.assertEqual(self.call('approve',{'review':'outdated'})[0],409)
        digest=preview['review']
        with ThreadPoolExecutor(2) as pool:
            codes=[f.result()[0] for f in [pool.submit(self.call,'approve',{'review':digest}) for _ in range(2)]]
        self.assertEqual(sorted(codes),[200,409])
        until=time.monotonic()+5
        while not self.call('state')[1]['activity'].get('run'):
            self.assertLess(time.monotonic(),until)
            time.sleep(.10)
        self.assertEqual(self.call('pause',{})[0],200)
        paused=self.wait('paused')
        self.assertEqual(paused['activity']['done'],1)
        reopened=Panel(self.panel.root,False)
        self.assertEqual(reopened.status()['activity']['status'],'paused')
        self.join_worker()
        self.assertEqual(self.call('resume',{})[0],200)
        done=self.wait('complete')
        self.assertEqual(done['activity']['completed'],3)
        self.assertEqual(done['activity']['done'],5)
        self.assertEqual(done['numbering']['next_number'],36)
        self.assertFalse(list(self.panel.root.rglob('*.mp4')))

    def test_numbering_collision_and_clearance_fail_closed(self):
        record={'number':40,'date':'2026-10-19','confirm':True}
        collision=self.panel.folders()['master']/'Vlog 41.mp4'
        collision.write_bytes(b'fixture')
        self.assertEqual(self.call('number',record)[0],409)
        self.assertEqual(collision.read_bytes(),b'fixture')
        self.assertEqual(self.call('number',dict(record,number=44))[0],200)
        self.assertEqual(self.call('state')[1]['numbering']['next_number'],45)
        clearance=self.call('clearance',{})[1]['result']
        self.assertFalse(clearance['safe_to_format'])
        self.assertFalse(clearance['safe_to_disconnect'])

    def test_live_requires_qualification_record(self):
        with self.assertRaisesRegex(ValueError,'not been approved'):
            Panel(self.panel.root,True)


if __name__=='__main__':
    unittest.main()
