"""Trusted catalog-ID offline actions; browser requests never supply paths."""
from contextlib import closing
from pathlib import Path
import sqlite3


def recording_action(root, capture_id, action):
    if action not in ('replay', 'reanalyze') or not 1 <= len(capture_id) <= 100:
        raise ValueError('Invalid recording action')
    root = Path(root).resolve()
    with closing(sqlite3.connect(root/'data'/'rf_atlas.sqlite', timeout=2)) as db:
        row = db.execute('SELECT path,signal_id FROM captures WHERE capture_id=?', (capture_id,)).fetchone()
        if row is None:
            raise ValueError('Recording not found')
        path = Path(row[0]).resolve()
        try:
            path.relative_to(root/'data'/'captures')
        except ValueError:
            raise ValueError('Recording outside local capture directory') from None
        # Browser actions have a smaller limit than trusted CLI reanalysis.
        budget = 8*1024*1024
        if action == 'replay':
            from .iq import replay
            result, _ = replay(path, max_bytes=budget)
            return {'action':action,'capture_id':capture_id,'result':result}
        from .identification import identify_recording,store_identification
        previous = db.execute('SELECT protocol FROM signals WHERE signal_id=?',(row[1],)).fetchone()
        result, meta = identify_recording(path, previous_protocol=previous[0] if previous else None,
                                         max_bytes=budget)
        from .runtime import load_settings
        settings=load_settings(root)
        key = store_identification(db,result,dict(meta,signal_id=row[1]),path,retain_frames=settings['retain_frames'],retain_identifiers=settings['retain_identifiers'])
        from .entities import private_result
        result=private_result(result,retain_frames=settings['retain_frames'],retain_identifiers=settings['retain_identifiers'])
        return {'action':action,'capture_id':capture_id,'run_key':key,'result':result}
