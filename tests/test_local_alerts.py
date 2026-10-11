"""Local alerts only emit new verified channel transitions, with bounded pages."""
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import closing
from rfatlas.storage import open_database
from rfatlas.local_alerts import changes
from rfatlas.runtime import load_settings,validate_settings


class LocalAlertTests(unittest.TestCase):
    def test_initialization_duplicates_candidates_and_pagination(self):
        with tempfile.TemporaryDirectory() as folder,closing(open_database(Path(folder))) as db:
            db.execute("INSERT INTO signals(signal_id) VALUES('toy')")
            def add(key,protocol,verified):
                payload=dict(protocol=protocol,protocol_verified=verified,private_content='must never appear')
                db.execute('INSERT INTO identification_runs VALUES(?,?,?,?,?,?,?)',
                           (key,'toy','2026-10-11T00:00:00Z','digest','test','private.cu8',json.dumps(payload)))
                db.commit()
            add('old','RDS',True)
            self.assertEqual(changes(db)['events'],[])
            add('repeat','RDS',True);add('candidate','DMR',False);add('verified','DMR',True)
            first=changes(db,1,2);self.assertEqual(first['events'],[]);self.assertEqual(first['cursor'],3)
            result=changes(db,first['cursor'],2)
            self.assertEqual(len(result['events']),1);self.assertEqual(result['events'][0]['protocol'],'DMR')
            self.assertNotIn('private',json.dumps(result));self.assertEqual(changes(db,result['cursor'])['events'],[])
            with self.assertRaises(ValueError):changes(db,999)
            with self.assertRaises(ValueError):changes(db,0,201)

    def test_alerts_default_off_and_strict_boolean(self):
        with tempfile.TemporaryDirectory() as folder:self.assertFalse(load_settings(Path(folder))['local_alerts'])
        self.assertTrue(validate_settings({'local_alerts':True})['local_alerts'])
        with self.assertRaises(ValueError):validate_settings({'local_alerts':'yes'})


if __name__=='__main__':unittest.main()
