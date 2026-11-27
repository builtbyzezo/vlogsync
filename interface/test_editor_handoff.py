import json
from pathlib import Path
import tempfile
import unittest

from panel import Panel
from editor_handoff import revision, title, retrieval_selection, retrieve_job, catalog, store, record_new_pairs, eligible_returns
from panel import read


class HandoffTests(unittest.TestCase):
    def setUp(self):
        base=Path(__file__).parent/'Runs'
        base.mkdir(exist_ok=True)
        temp=tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(temp.cleanup)
        self.panel=Panel(Path(temp.name),False)

    def act(self, name, **data):
        return self.panel.action('editor-'+name,data)

    def rows(self):
        return {r['n']:r for r in self.act('list')['rows']}

    def test_confirmed_exclusion_and_resume_through_manual_transfer(self):
        self.assertEqual(self.rows()[281]['state'],'submitted')
        self.assertEqual(self.rows()[282]['state'],'uncertain')
        with self.assertRaises(ValueError):
            self.act('prepare',numbers=[282])
        self.act('mark-unshared',n=282,checked_space=True)
        with self.assertRaises(ValueError):
            self.act('prepare',numbers=[281,282])
        self.act('prepare',numbers=[282])
        self.assertEqual(self.rows()[281]['state'],'submitted')
        self.assertEqual(self.rows()[282]['state'],'prepared')
        self.panel=Panel(self.panel.root,False)
        self.act('submitted',n=282,confirmed=True)
        retrieve_job(self.panel,[{'n':282}])
        self.assertTrue(self.rows()[282]['master_verified'])
        with self.assertRaises(ValueError):
            self.act('approve',n=282,revision='stale',title='Approved edit')
        self.act('approve',n=282,revision=self.rows()[282]['revision'],title='Approved edit')
        self.act('export',numbers=[282])
        self.assertEqual(self.rows()[282]['state'],'exported')
        with self.assertRaises(ValueError):
            self.act('export',numbers=[282])

    def test_completed_download_matching_holds_ambiguity_and_checks_cloud_binding(self):
        state={'281':{'n':281,'state':'submitted','cloud_entry_id':74}}
        matches=[
            {'draft_name':'Vlog 281 (1)','draft_cloud_last_action_download':True,'tm_draft_cloud_entry_id':74},
            {'draft_name':'Vlog 281 (2)','draft_cloud_last_action_download':True,'tm_draft_cloud_entry_id':74},
            {'draft_name':'Vlog 281 (3)','draft_cloud_last_action_download':True,'tm_draft_cloud_entry_id':75},
            {'draft_name':'Vlog 281 (4)','draft_cloud_last_action_download':False,'tm_draft_cloud_entry_id':74}]
        with self.assertRaises(ValueError):
            retrieval_selection(state,matches,[{'n':281}])
        with self.assertRaises(ValueError):
            retrieval_selection(state,matches,[{'n':281,'returned':'Vlog 281 (3)'}])
        self.assertEqual(retrieval_selection(state,matches,[{'n':281,'returned':'Vlog 281 (2)'}]),{281:'Vlog 281 (2)'})
        self.assertEqual(retrieval_selection(state,matches[:1],[{'n':281}]),{281:'Vlog 281 (1)'})

    def test_default_batch_preserves_exceptions_and_historical_holds(self):
        record_new_pairs(self.panel,[281,282,283])
        self.assertEqual(self.rows()[284]['state'],'uncertain')
        self.act('prepare',numbers=[282,283])
        with self.assertRaises(ValueError):
            self.act('confirm-batch',numbers=[282,284],confirmed=True)
        self.assertEqual(self.rows()[282]['state'],'prepared')
        self.act('confirm-batch',numbers=[282],confirmed=True)
        rows=self.rows()
        self.assertEqual(rows[282]['state'],'submitted')
        self.assertEqual(rows[283]['state'],'prepared')
        self.assertEqual(rows[281]['state'],'submitted')
        saved=read(store(self.panel),{})
        self.assertEqual(eligible_returns(saved,catalog(self.panel)),[{'n':282},{'n':281}])
        retrieve_job(self.panel,[{'n':282},{'n':281}])
        rows=self.rows()
        picks=[{'n':n,'title':f'Week {n}','revision':rows[n]['revision']} for n in (281,282)]
        rejected=[picks[0],dict(picks[1],revision='stale')]
        with self.assertRaises(ValueError):
            self.act('approve-export',picks=rejected,confirmed=True)
        self.assertEqual(self.rows()[281]['state'],'review')
        self.act('approve-export',picks=picks,confirmed=True)
        self.assertEqual([self.rows()[n]['state'] for n in (281,282)],['exported','exported'])

    def test_revision_includes_effect_and_rejects_unsafe_title(self):
        folder=self.panel.root
        content=folder/'draft_content.json'
        content.write_text(json.dumps({'tracks':[1],'effects':[1],'update_time':1}))
        start=revision(folder)
        content.write_text(json.dumps({'tracks':[1],'effects':[1],'update_time':2}))
        self.assertEqual(revision(folder),start)
        content.write_text(json.dumps({'tracks':[1],'effects':[2],'update_time':2}))
        self.assertNotEqual(revision(folder),start)
        for name in ('../Escape','a/b','bad.','a:b'):
            with self.assertRaises(ValueError):
                title(name)
