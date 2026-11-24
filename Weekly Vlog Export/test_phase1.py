import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import content_probe
import numbering
import wve_safety as r


class FilesystemTests(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.base = Path(self.workspace.name)
        self.source = self.base/'source.mp4'
        self.final = self.base/'Vlog 260.mp4'
        self.pending = self.base/'.owned.partial'
        self.source.write_bytes(b'a disposable local media stand-in')
        self.record = {}

    def test_commit_and_resume(self):
        partial = self.source.read_bytes()[:7]
        self.pending.write_bytes(partial)
        states = []
        r.publish_file(self.source,self.final,self.pending,self.record,
                       lambda:states.append(self.record['state']))
        self.assertEqual(states,['staging','committing','verified'])
        self.assertEqual(self.final.read_bytes(),self.source.read_bytes())
        r.publish_file(self.source,self.final,self.pending,self.record,lambda:None)

    def test_transaction_recovers_after_rename(self):
        self.final.write_bytes(self.source.read_bytes())
        self.record.update(state='committing',signature=r.identity(self.source),sha256=r.digest(self.source))
        r.publish_file(self.source,self.final,self.pending,self.record,lambda:None)
        self.assertEqual(self.record['state'],'verified')

    def test_reject_existing_unassociated_file(self):
        self.final.write_bytes(b'existing export')
        with self.assertRaises(FileExistsError):
            r.publish_file(self.source,self.final,self.pending,self.record,lambda:None)
        self.assertEqual(self.final.read_bytes(),b'existing export')

    def test_corrupt_prefix(self):
        self.pending.write_bytes(b'bad')
        with self.assertRaisesRegex(RuntimeError,'prefix'):
            r.publish_file(self.source,self.final,self.pending,self.record,lambda:None)
        self.assertFalse(self.final.exists())

    def test_insufficient_storage(self):
        usage = type('Disk',(),{'free':1})()
        with patch.object(r.shutil,'disk_usage',return_value=usage), self.assertRaisesRegex(RuntimeError,'storage'):
            r.publish_file(self.source,self.final,self.pending,self.record,lambda:None)
        self.assertFalse(self.final.exists())

    def test_consistent_snapshot(self):
        r.snapshot(self.base,'Production-fixture',{'sources':['one']},{'state':'queued'})
        item, = (self.base/'Records'/'Recovery snapshots'/'Production-fixture').glob('*.json')
        self.assertEqual(json.loads(item.read_bytes())['queue']['state'],'queued')


class SafetyTests(unittest.TestCase):
    def test_native_recovery_rejects_present_pid_when_image_query_cannot_verify_it(self):
        import crash_recovery as recovery
        response=subprocess.CompletedProcess([],0,stdout='"CapCut.exe","123"')
        with (patch.object(recovery,'process_image',return_value=None),
              patch.object(recovery.subprocess,'run',return_value=response)):
            with self.assertRaisesRegex(RuntimeError,'PID is still present'):
                recovery.recover_verified_exit(None,None,None,None,None,'Master','2026-01-01',123)

    def test_fresh_workspace_has_own_zero_ledger(self):
        import numbering
        from setup_vlogsync import install
        with tempfile.TemporaryDirectory() as folder:
            root=install(Path(folder)/'VlogSync',0,None,'UTC')
            self.assertEqual(numbering.status(numbering.read(root/'Configuration'/'numbering.json'))['next_number'],1)
            with self.assertRaises(FileExistsError):
                install(root,0,None,'UTC')

    def test_new_controller_uses_title_and_no_existing_process_vlogs_adapter(self):
        import sys
        import weekly_vlog_export as w
        self.assertEqual(w.camera.__name__,'camera_media')
        self.assertFalse({'vlog_core','weekly_camera','editor_proxy','cloud_delivery','cloud_status','local_metadata'} & sys.modules.keys())

    def test_clean_boot_uses_candidate_module(self):
        subprocess.run(['python','-B','-c',
                        'import weekly_vlog_export as w; import content_probe, filesystem_delivery; assert w.reliability.__name__=="wve_safety"'],
                       cwd=Path(__file__).parent,check=True,timeout=20)

    def test_window_lock_and_display_geometry_guards(self):
        import desktop_controls as ui
        screen=object.__new__(ui.Screen)
        screen.window=42
        screen.bounds=ui.Box(0,0,1920,1080)
        with patch.object(ui,'interactive_desktop',return_value=False):
            with self.assertRaisesRegex(RuntimeError,'locked'):
                screen.check()
        with (patch.object(ui,'interactive_desktop',return_value=True),
              patch.object(ui.user,'GetForegroundWindow',return_value=42),
              patch.object(ui,'rect',return_value=ui.Box(0,0,1280,720))):
            with self.assertRaisesRegex(RuntimeError,'moved'):
                screen.check()

    def test_disposable_camera_removal_and_reconnection(self):
        import weekly_vlog_export as w
        with tempfile.TemporaryDirectory() as work:
            camera=Path(work)/'Camera01'
            camera.mkdir()
            clip=camera/'VID_20260101_123456_001.mp4'
            clip.write_bytes(b'fixture')
            plan=dict(camera=str(camera),projects=[dict(sources=[dict(path=str(clip),stamp=r.identity(clip))])])
            w.camera_check(plan)
            moved=camera.rename(Path(work)/'Disconnected')
            with self.assertRaisesRegex(RuntimeError,'disconnected'):
                w.camera_check(plan)
            moved.rename(camera)
            w.camera_check(plan)
            clip.write_bytes(b'a different recording with a reused name')
            with self.assertRaisesRegex(RuntimeError,'changed'):
                w.camera_check(plan)

    def test_power_requires_verified_saved_success(self):
        with patch.object(r.subprocess,'run') as run:
            for fields in ({'succeeded':False},{'pending':True},{'verified_publication':False},{'saved':False}):
                status=dict(succeeded=True,pending=False,verified_publication=True,saved=True)
                status.update(fields)
                with self.assertRaises(RuntimeError):
                    r.completion_power('shutdown',**status)
            run.assert_not_called()
            r.completion_power('shutdown',succeeded=True,pending=False,verified_publication=True,saved=True)
            self.assertEqual(run.call_args.args[0],['shutdown.exe','/s','/t','0'])

    def test_retry_refuses_partial_and_confirmed_failure_can_be_reserved(self):
        with tempfile.TemporaryDirectory() as work:
            target=Path(work)
            state={'rounds':{'Master':{'2026-01-01':{'state':'failed'}}}}
            (target/'unknown.tmp').touch()
            with self.assertRaises(RuntimeError):
                r.safe_retry(state,'Master','2026-01-01',target,'Vlog 260.mp4')
            clean=target/'clean'
            clean.mkdir()
            r.safe_retry(state,'Master','2026-01-01',clean,'Vlog 260.mp4')
            self.assertNotIn('2026-01-01',state['rounds']['Master'])
            self.assertEqual(state['retry_history']['Master']['2026-01-01']['retry_count'],1)

    def test_isolated_verification_failure_leaves_other_output_complete(self):
        import weekly_vlog_export as w
        with tempfile.TemporaryDirectory() as location:
            base = Path(location)
            paths = {name:base/(name+'.mp4') for name in ('bad','good')}
            for path in paths.values():
                path.write_bytes(b'fixture')
            rows = {day:{'state':'queued'} for day in paths}
            seen = {day:(r.identity(path),0) for day,path in paths.items()}
            plan = {'projects':[{'date':day} for day in paths]}
            def probe(path,*_):
                if path.stem=='bad':
                    raise RuntimeError('Wrong finalized codec')
                return {'path':str(path)}
            with patch.object(w.exports,'target',side_effect=lambda day,role:paths[day]),patch.object(w,'output_info',side_effect=probe):
                w.reconcile(plan,'Master',rows,seen)
            self.assertEqual(rows['bad']['state'],'failed')
            self.assertEqual(rows['good']['state'],'complete')
            self.assertEqual(paths['bad'].read_bytes(),b'fixture')

    def test_clearance_requires_exact_inventory_and_content(self):
        expected = {'Clip.mp4':'identity'}
        for actual,proof in (({},{}),({'Clip.mp4':'changed'},expected),(expected,{}),
                             ({**expected,'new.mp4':'new'},expected)):
            self.assertFalse(r.clearance(expected,actual,proof)['safe_to_format'])
        self.assertTrue(r.clearance(expected,expected,expected)['safe_to_format'])
        self.assertFalse(r.clearance(expected,expected,expected,native_pending=True)['safe_to_disconnect'])

    def test_reservation_reuse_and_changed_timeline(self):
        ledger = dict(last_assigned={'number':9,'recording_date':'2026-01-01'},assignments=[])
        items = [dict(date='2026-01-02',identities=['a','b'])]
        bound = numbering.bind(items,ledger,reserve=True)
        self.assertEqual(10,bound[0]['vlog_number'])
        self.assertEqual(bound,numbering.bind(items,ledger))
        with self.assertRaises(RuntimeError):
            numbering.bind([dict(date='2026-01-02',identities=['b','a'])],ledger,reserve=True)

    def test_diagnostic_sanitization(self):
        error = RuntimeError('email name@example.org at C:/Users/private/footage.mp4')
        text = str(r.diagnostic(error,'Production-test'))
        self.assertNotIn('name@example.org',text)
        self.assertNotIn('C:/Users/private',text)

    def test_small_media_discrepancies(self):
        import weekly_vlog_export as weekly
        ffmpeg = weekly.exports.FFMPEG
        with tempfile.TemporaryDirectory() as location:
            base = Path(location)
            normal, black, dark = (base/(name+'.mp4') for name in ('normal','black','dark'))
            for path,filter_name,audio in ((normal,'testsrc2',True),(black,'color=c=black',False),
                                           (dark,'color=c=0x080808',False)):
                args = ['-nostdin','-v','error','-f','lavfi','-i',filter_name+':s=160x90:r=30' if '=' in filter_name
                        else filter_name+'=s=160x90:r=30']
                if audio:
                    args += ['-f','lavfi','-i','sine=frequency=400']
                subprocess.run([str(ffmpeg),*args,'-t','2','-c:v','libx264',str(path)],
                               check=True,capture_output=True,timeout=30)
            self.assertEqual([],content_probe.compare(ffmpeg,dark,dark,0,2))
            problems=content_probe.compare(ffmpeg,normal,black,0,2)
            self.assertTrue(any('picture' in item['reason'] for item in problems))
            self.assertTrue(any('audible' in item['reason'] for item in problems))


if __name__=='__main__':
    unittest.main()
