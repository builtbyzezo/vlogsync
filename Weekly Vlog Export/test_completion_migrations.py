import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import drive_consolidation as layout
import relocated_completion as completion


class CompletionMigrations(unittest.TestCase):
    def test_fresh_install_requires_no_legacy_bindings(self):
        with TemporaryDirectory() as tmp, patch.object(layout, 'STATE', Path(tmp)):
            with patch.dict('os.environ', {'LOCALAPPDATA': tmp}):
                completion.merge_completed(Path(tmp) / 'VlogSync', None, {}, {})

    def test_verified_binding_resolves_destination_and_rejects_changed_digest(self):
        with TemporaryDirectory() as tmp, patch.object(layout, 'STATE', Path(tmp)):
            root = Path(tmp)
            old = root / 'Keep' / '1080p' / 'Example.mp4'
            data = {'schema': 1, 'files': {'Keep/1080p/Example.mp4': {
                'path': '1080p Master Vlogs/Example.mp4', 'sha256': 'demo', 'stamp': [10, 20]}}}
            (root / 'drive-organization-v1.json').write_text(json.dumps(data), encoding='utf-8')
            path, stamp = layout.mounted_path(root, old, 'Master', 'demo', [10, 1])
            self.assertEqual(path, root / '1080p Master Vlogs' / 'Example.mp4')
            self.assertEqual(stamp, [10, 20])
            with self.assertRaises(RuntimeError):
                layout.mounted_path(root, old, 'Master', 'different', [10, 1])

    def test_missing_legacy_media_requires_migration_record(self):
        with TemporaryDirectory() as tmp, patch.object(layout, 'STATE', Path(tmp)):
            root = Path(tmp)
            with self.assertRaises(RuntimeError):
                layout.mounted_path(root, root / 'missing.mp4', 'Proxy', 'demo', [10, 1])


if __name__ == '__main__':
    unittest.main()
