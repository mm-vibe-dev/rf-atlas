"""Metadata-only local transitions; no content, identifiers or network delivery."""
def changes(db,after=None,limit=100):
    if type(limit) is not int or not 1<=limit<=200:raise ValueError('Invalid alert limit')
    latest=db.execute('SELECT COALESCE(MAX(rowid),0) FROM identification_runs').fetchone()[0]
    if after is None:return dict(cursor=latest,events=[],initialized=True)
    if type(after) is not int or not 0<=after<=latest:raise ValueError('Invalid alert cursor')
    rows=db.execute("SELECT rowid,signal_id,created,json_extract(result,'$.protocol'),"
                    "json_extract(result,'$.protocol_verified') FROM identification_runs "
                    "WHERE rowid>? ORDER BY rowid LIMIT ?",(after,limit)).fetchall()
    events=[]
    for rowid,signal_id,created,protocol,verified in rows:
        if verified!=1 or not protocol or protocol=='UNKNOWN':continue
        earlier=db.execute("SELECT 1 FROM identification_runs WHERE signal_id=? AND rowid<? "
                           "AND json_extract(result,'$.protocol')=? AND json_extract(result,'$.protocol_verified')=1 LIMIT 1",
                           (signal_id,rowid,protocol)).fetchone()
        if earlier:continue
        events.append(dict(kind='NEW_VERIFIED_PROTOCOL',signal_id=signal_id,protocol=protocol,analyzed_at=created))
    return dict(cursor=rows[-1][0] if rows else latest,events=events,initialized=False,
                meaning='New local verification for a channel; analysis time, not new transmitter identity.')
