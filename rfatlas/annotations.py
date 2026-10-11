"""Private operator annotations, deliberately separate from RF evidence."""
import json
from datetime import datetime,timezone


def validate(value):
    if not isinstance(value,dict) or set(value)!={'name','notes','tags','favorite'}:
        raise ValueError('Require name, notes, tags and favorite')
    if any(not isinstance(value[k],str) or len(value[k])>limit or '\x00' in value[k]
           for k,limit in (('name',120),('notes',4000))):
        raise ValueError('Annotation text exceeds limits')
    tags=value['tags']
    if not isinstance(tags,list) or len(tags)>20 or any(not isinstance(t,str) or not t.strip() or len(t)>48 or '\x00' in t for t in tags):
        raise ValueError('Invalid annotation tags')
    if type(value['favorite']) is not bool:raise ValueError('Favorite must be boolean')
    return dict(value,tags=list(dict.fromkeys(t.strip() for t in tags)))


def read(db, signal_id):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='signal_annotations'").fetchone():
        return dict(name='',notes='',tags=[],favorite=False)
    row=db.execute('SELECT value FROM signal_annotations WHERE signal_id=?',(signal_id,)).fetchone()
    return json.loads(row[0]) if row else dict(name='',notes='',tags=[],favorite=False)


def save(db, signal_id, value):
    checked=validate(value)
    if not db.execute('SELECT 1 FROM signals WHERE signal_id=?',(signal_id,)).fetchone():
        raise ValueError('Unknown signal')
    with db:
        db.execute('INSERT INTO signal_annotations VALUES(?,?,?) ON CONFLICT(signal_id) DO UPDATE SET value=excluded.value,updated=excluded.updated',
            (signal_id,json.dumps(checked,ensure_ascii=False),datetime.now(timezone.utc).isoformat()))
    return checked
