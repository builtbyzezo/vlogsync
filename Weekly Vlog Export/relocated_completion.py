"""Recognize relocated numbered exports using the sealed production records."""
import hashlib
import json
import os
from pathlib import Path

ROLE_DIR = {'Master': '1080p Masters', 'Proxy': '720p Editor Proxies'}


def merge_completed(manager, source_stamp, known, versions):
    if manager is None:
        return
    proof_path = Path(os.environ['LOCALAPPDATA']) / 'VlogSync' / 'completion-migration-v1.json'
    if not proof_path.is_file():
        from drive_consolidation import STATE
        # An ordinary fresh install has neither a binding nor the sealed old delivery.
        if (STATE / 'drive-organization-v1.json').is_file():
            raise RuntimeError('Restore the numbered completion binding before export.')
        for run in (manager / 'Records' / 'Runs').glob('Production-*'):
            queue = run / 'queue.json'
            if queue.is_file():
                rows = json.loads(queue.read_bytes()).get('delivery', {})
                if any('folder_id' in row for group in rows.values() for row in group.values()):
                    raise RuntimeError('Existing server publication requires its completion binding.')
        return
    proof = json.loads(proof_path.read_bytes())
    if proof['schema'] != 1 or not proof['run'].startswith('Production-') or Path(proof['run']).name != proof['run']:
        raise RuntimeError('Invalid numbered completion migration record')
    root = manager / 'Records' / 'Runs' / proof['run']
    docs = {}
    for kind in ('plan', 'queue'):
        data = (root / f'{kind}.json').read_bytes()
        if hashlib.sha256(data).hexdigest() != proof[f'{kind}_sha256']:
            raise RuntimeError('Numbered production record changed. Review before retrying exports.')
        docs[kind] = json.loads(data)

    if docs['plan']['mode'] != 'weekly' or not docs['queue']['complete']:
        raise RuntimeError('The migrated production run is not complete')
    for item in docs['plan']['projects']:
        day = item['date']
        name = f'Vlog {item["vlog_number"]}.mp4'
        for role, role_dir in ROLE_DIR.items():
            record = docs['queue']['delivery'][role][day]
            receipt = proof['roles'][role][day]
            path = Path('Exports', role_dir, name)
            if (record['state'] != 'verified' or record['name'] != name
                or receipt['path'] != path.as_posix()
                or (receipt['id'], receipt['parent'], receipt['sha256'], receipt['stamp'][0]) !=
                   (record['id'], record['folder_id'], record['sha256'], record['size'])):
                raise RuntimeError(f'Numbered completion identity changed: {day} {role}')
            from drive_consolidation import STATE, mounted_path
            original = (manager.parent / 'YouTube Vlog Manager' if (STATE / 'drive-organization-v1.json').is_file() else manager) / path
            destination, stamp = mounted_path(manager.parent, original, role, receipt['sha256'], receipt['stamp'])
            if not destination.is_file() or source_stamp(destination) != stamp:
                raise RuntimeError(f'Numbered destination changed or unavailable: {destination}')
            known.setdefault(tuple(item['identities']), {})[role] = dict(
                path=str(destination), signature=stamp, sha256=receipt['sha256'],
                method='Verified permanent numbered Google Drive publication')
            versions.setdefault(day, set()).add(role)
