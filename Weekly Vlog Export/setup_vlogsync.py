"""Initialize an independent VlogSync installation without overwriting data."""
import argparse
from datetime import date
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo


def install(workspace,number,recorded,zone):
    ZoneInfo(zone)
    if number<0 or (number and not recorded):
        raise ValueError('Use zero for a new ledger, or give a checked number and its recording date.')
    if recorded:
        date.fromisoformat(recorded)
    workspace=workspace.resolve()
    fields=workspace/'Configuration'
    fields.mkdir(parents=True,exist_ok=True)
    ledger=fields/'numbering.json'
    marker=fields/'controller.json'
    clock=fields/'recording.json'
    if any(p.exists() for p in (ledger,marker,clock)):
        raise FileExistsError('Existing configuration preserved. Use this setup only for a fresh workspace.')
    for name in ('Extra Footage','1080p Master Vlogs','720p Editor Proxies'):
        (workspace.parent/name).mkdir(exist_ok=True)
    (workspace/'Records').mkdir(exist_ok=True)
    content=((ledger,dict(schema=1,observed_through=number,last_assigned=dict(
        number=number,recording_date=recorded),assignments=[])),(clock,dict(timezone=zone)),
        (marker,dict(workspace='weekly-vlog-export-vlogsync',extra_footage='Extra Footage',
                     filesystem_exports={'Master':'1080p Master Vlogs','Proxy':'720p Editor Proxies'})))
    for path,data in content:
        with path.open('x',encoding='utf-8') as stream:
            json.dump(data,stream,indent=2)
    return workspace


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workspace',type=Path)
    parser.add_argument('--last-number',required=True,type=int)
    parser.add_argument('--recorded')
    parser.add_argument('--timezone',required=True)
    args=parser.parse_args()
    root=install(args.workspace,args.last_number,args.recorded,args.timezone)
    print(f"PowerShell session: $env:WEEKLY_VLOG_MANAGER_ROOT = '{str(root).replace(chr(39),chr(39)*2)}'")


if __name__=='__main__':
    main()
