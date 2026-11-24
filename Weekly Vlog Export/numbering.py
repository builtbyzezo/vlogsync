"""Persist one vlog number per confirmed ordered timeline."""
from datetime import date
import hashlib
import json
import re


def read(path):
    ledger = json.loads(path.read_bytes())
    if ledger.get('schema')!=1 or type(ledger.get('observed_through')) is not int:
        raise RuntimeError('Unknown vlog numbering schema')
    anchor, rows = ledger['last_assigned'], ledger['assignments']
    if anchor['recording_date'] is not None:
        date.fromisoformat(anchor['recording_date'])
    numbers = [row['number'] for row in rows]
    days = [row['recording_date'] for row in rows]
    fingerprints = [row['ordered_identity_sha256'] for row in rows]
    for day in days:
        date.fromisoformat(day)
    if (type(anchor['number']) is not int or anchor['number']<ledger['observed_through']
            or any(type(n) is not int or not ledger['observed_through']<n<=anchor['number'] for n in numbers)
            or len(set(numbers))!=len(rows) or len(set(days))!=len(rows)
            or len(set(fingerprints))!=len(rows)
            or any(not re.fullmatch(r'[a-f0-9]{64}',value) for value in fingerprints)
            or any(row['number']==anchor['number'] and row['recording_date']!=anchor['recording_date'] for row in rows)):
        raise RuntimeError('Duplicate or inconsistent vlog numbering. Review the ledger before exporting.')
    return ledger


def fingerprint(identities):
    return hashlib.sha256(json.dumps(identities,separators=(',',':')).encode()).hexdigest()


def bind(projects, ledger, *, reserve=False):
    by_day = {row['recording_date']:row for row in ledger['assignments']}
    by_source = {row['ordered_identity_sha256']:row for row in ledger['assignments']}
    next_number = ledger['last_assigned']['number']
    numbered = []
    for item in sorted(projects,key=lambda item:item['date']):
        day, identity = item['date'],fingerprint(item['identities'])
        row = by_day.get(day)
        if row:
            if row['ordered_identity_sha256']!=identity:
                raise RuntimeError(f'Vlog {row["number"]} is already reserved for a different timeline on {day}')
        elif identity in by_source:
            raise RuntimeError(f'This ordered timeline is already assigned on {by_source[identity]["recording_date"]}')
        else:
            if not reserve:
                raise RuntimeError(f'No approved number reserved for {day}. Preview and explicitly reserve the batch.')
            next_number += 1
            row = dict(number=next_number,recording_date=day,ordered_identity_sha256=identity)
            ledger['assignments'].append(row)
            by_day[day] = by_source[identity] = row
            ledger['last_assigned'] = dict(number=next_number,recording_date=day)
        number = row['number']
        numbered.append(dict(item,vlog_number=number,output_stem=f'Vlog {number}',title=f'WVE Vlog {number}'))
    return numbered


def edit_anchor(ledger, number, recording_date):
    if number<max([ledger['observed_through'],*(row['number'] for row in ledger['assignments'])]):
        raise RuntimeError('The counter cannot go below a historical or reserved number')
    date.fromisoformat(recording_date)
    for row in ledger['assignments']:
        if row['number']==number and row['recording_date']!=recording_date:
            raise RuntimeError('This recording date conflicts with an existing assignment')
    ledger['last_assigned'] = dict(number=number,recording_date=recording_date)


def status(ledger):
    last = ledger['last_assigned']
    return dict(last_assigned=last,next_number=last['number']+1)
