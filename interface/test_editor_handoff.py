import json
from pathlib import Path
import tempfile
import unittest

from panel import Panel
from editor_handoff import revision, title


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
        self.act('prepare',numbers=[281,282],checked_space=True)
        self.assertEqual(self.rows()[281]['state'],'submitted')
        self.assertEqual(self.rows()[282]['state'],'prepared')
        self.panel=Panel(self.panel.root,False)
        self.act('submitted',n=282,confirmed=True)
        self.act('retrieve',n=282,returned='Vlog 282 (1)')
        with self.assertRaises(ValueError):
            self.act('approve',n=282,revision='stale',title='Approved edit')
        self.act('approve',n=282,revision=self.rows()[282]['revision'],title='Approved edit')
        self.act('export',numbers=[282])
        self.assertEqual(self.rows()[282]['state'],'exported')
        with self.assertRaises(ValueError):
            self.act('export',numbers=[282])

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
