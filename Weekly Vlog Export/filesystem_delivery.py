"""Ordinary filesystem publication for candidate runs."""
from pathlib import Path
import re

from wve_safety import publish_file


def folders(weekly):
    config = weekly.exports.draft.read(weekly.MANAGER_ROOT/'Configuration'/'controller.json')
    names = config.get('filesystem_exports',{'Master':'1080p Master Vlogs','Proxy':'720p Editor Proxies'})
    targets = {role:weekly.deployment.under(weekly.MANAGER_ROOT.parent,names[role])
               for role in weekly.exports.ROUND}
    if len(set(targets.values()))!=2:
        raise RuntimeError('Master and proxy require different destinations')
    for folder in targets.values():
        weekly.exports.draft.check_path(folder)
    return targets


def check_numbers(weekly,numbers):
    config = weekly.exports.draft.read(weekly.MANAGER_ROOT/'Configuration'/'controller.json')
    destinations = set(folders(weekly).values())
    destinations.update((weekly.MANAGER_ROOT/'Exports'/'1080p Masters',
                         weekly.MANAGER_ROOT/'Exports'/'720p Editor Proxies',
                         weekly.MANAGER_ROOT.parent/'Keep'/'1080p',weekly.MANAGER_ROOT.parent/'Keep'/'720p',
                         Path.home()/'OneDrive'/'Youtube'/'Vlog Exports',Path.home()/'OneDrive'/'Youtube'/'Editor Proxies',
                         Path.home()/'Videos'/'Historical Vlogs'))
    destinations.update(Path(p) for p in config.get('additional_numbering_folders',[]))
    for folder in destinations:
        for path in folder.iterdir() if folder.is_dir() else ():
            found = re.match(r'^vlog\s*0*(\d+)(?=\D|$)',path.name,re.I)
            if found and int(found[1]) in numbers:
                raise RuntimeError(f'Number already used in a known destination: {path.name}')


def preflight(weekly,plan,state):
    for role,folder in folders(weekly).items():
        for item in plan['projects']:
            final = folder/f'Vlog {item["vlog_number"]}.mp4'
            if final.exists():
                row = state.get('delivery',{}).get(role,{}).get(item['date'],{})
                if (row.get('destination')!=str(final) or
                        row.get('transaction',{}).get('state') not in ('committing','verified')):
                    raise FileExistsError(final)


def publish(weekly,plan,state,journal):
    for role,folder in folders(weekly).items():
        if not folder.is_dir():
            raise RuntimeError(f'Destination unavailable. Verified renders are retained: {folder}')
        rows = state.setdefault('delivery',{}).setdefault(role,{})
        for item in plan['projects']:
            if weekly.camera.read_json(journal.with_name('control.json'),{}).get('request') in ('pause','cancel'):
                raise RuntimeError('Publication stopped at a file boundary by the operator')
            day = item['date']
            output = state['rounds'][role][day]
            source = Path(output['metadata']['path'])
            if weekly.exports.draft.source_stamp(source)!=output['signature']:
                raise RuntimeError(f'Local verified file changed: {source}')
            final = folder/f'Vlog {item["vlog_number"]}.mp4'
            record = rows.setdefault(day,{})
            if record and record.get('destination')!=str(final):
                raise RuntimeError('Publication destination changed during recovery')
            if final.exists() and not record:
                raise FileExistsError(final)
            transaction = record.setdefault('transaction',{})
            record['destination'] = str(final)
            partial = folder/f'.{final.stem}.{journal.parent.name}.partial'
            publish_file(source,final,partial,transaction,lambda:weekly.save(journal,state))
