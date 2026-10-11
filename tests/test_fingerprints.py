"""Independent fixtures for characterization, clock provenance and broad energy."""
from pathlib import Path
from datetime import datetime, timezone
import sqlite3
import tempfile
import unittest
import csv
import numpy as np
from rfatlas.fingerprints import spectral_statistics, spectral_fingerprint, similarity, iq_fingerprint
from rfatlas.timestamps import receiver_utc, aware_utc
from rfatlas.storage import open_database
from rfatlas.detection import detect, detect_survey


class CharacterizationTests(unittest.TestCase):
    def test_entropy_is_not_a_protocol_or_encryption_detector(self):
        flat=spectral_statistics(np.ones(128))
        carrier=spectral_statistics(np.r_[1.,np.full(127,1e-10)])
        self.assertAlmostEqual(flat['spectral_entropy'],1)
        self.assertAlmostEqual(flat['spectral_flatness'],1)
        self.assertLess(carrier['spectral_entropy'],.001)
        self.assertGreater(carrier['spectral_concentration'],.999)
        with self.assertRaises(ValueError):spectral_statistics([1,2,float('nan')])
        with self.assertRaises(ValueError):spectral_statistics([1,2,float('-inf')])
        self.assertIsNone(spectral_statistics([0,0,0])['spectral_entropy'])
        rng=np.random.default_rng(8182)
        noise=iq_fingerprint(rng.normal(size=8192)+1j*rng.normal(size=8192),250000)
        tone=iq_fingerprint(np.exp(2j*np.pi*12000*np.arange(8192)/250000),250000)
        self.assertGreater(noise['spectral_entropy'],tone['spectral_entropy'])
        self.assertAlmostEqual(tone['carrier_offset_hz'],12000,places=5)

    def test_conservative_matching_and_indistinguishable_emitter_collision(self):
        row=dict(centers=np.arange(100)*1000+500,power=-60+20*np.exp(-((np.arange(100)-40)/4)**2),step=1000)
        peak=dict(low=36000,high=45000,center=40500,peak=20)
        a=spectral_fingerprint(row,peak);a['gain_db']=15.7
        self.assertEqual(similarity(a,a),1)
        for changes in (dict(center_hz=90000),dict(gain_db=20),dict(resolution_hz=5000),dict(bandwidth_hz=25000),dict(shape=[0]*32)):
            self.assertIsNone(similarity(a,dict(a,**changes)))
        # A second physically different emitter can have identical features.
        # This deliberate false identity match is why identity is never asserted.
        self.assertEqual(similarity(a,dict(a)),1)

    def test_broad_candidate_does_not_modify_frozen_narrow_detector(self):
        x=np.arange(2000);power=-70+25*np.exp(-((x-800)/150)**2)
        old=detect(100000000,1000,power)
        broad=detect_survey(100000000,1000,power)
        self.assertEqual(old,[])
        self.assertEqual(len(broad),1)
        self.assertEqual(broad[0]['method'],'BROAD_PROMINENCE_CANDIDATE')
        self.assertFalse(detect_survey(0,1000,np.linspace(-80,-40,2000)))
        for seed in range(20):
            self.assertFalse(detect_survey(0,1000,-60+np.random.default_rng(seed).normal(0,.3,2000)))

    def test_new_clock_conversion_and_legacy_migration_preserve_original(self):
        raw='2026-01-01T03:00:00+02:00'
        self.assertEqual(aware_utc(raw),'2026-01-01T01:00:00+00:00')
        self.assertIsNone(aware_utc('2026-01-01 03:00:00'))
        local=datetime.now().isoformat()
        value,basis=receiver_utc(local,datetime.now(timezone.utc).isoformat())
        self.assertEqual(basis,'RECEIVER_HOST_LOCAL_CLOCK')
        self.assertLess(abs((datetime.fromisoformat(value)-datetime.now(timezone.utc)).total_seconds()),2)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);db=open_database(root)
            db.execute("INSERT INTO signals(signal_id) VALUES('s')")
            for observed in (raw,'2026-01-01 03:00:00'):
                db.execute("INSERT INTO spectral_observations(signal_id,observed) VALUES('s',?)",(observed,))
            db.execute('PRAGMA user_version=7');db.commit();db.close()
            db=open_database(root)
            rows=db.execute('SELECT observed,observed_original,observed_utc,timestamp_basis FROM spectral_observations ORDER BY observation_id').fetchall()
            self.assertEqual(rows[0],(raw,raw,'2026-01-01T01:00:00+00:00','LEGACY_EXPLICIT_OFFSET'))
            self.assertEqual(rows[1][0],rows[1][1]);self.assertIsNone(rows[1][2])
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok');db.close()
            self.assertEqual(len(list((root/'data/backups').glob('catalog-before-1.2-*.sqlite'))),1)

    def test_ingestion_is_idempotent_but_distinct_visits_remain_observations(self):
        from rfatlas.legacy import ingest
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);db=open_database(root)
            for run in ('first','second'):db.execute("INSERT INTO survey_runs(run_id,settings) VALUES(?,'{}')",(run,))
            path=root/'fixture.csv';power=np.full(100,-60.);power[30:35]=-40
            with path.open('w',newline='') as stream:csv.writer(stream).writerow(['2026-01-01','12:00:00Z',100000000,100100000,1000,100,*power])
            with db:
                ingest(db,'first',path,100000000,100100000)
                ingest(db,'first',path,100000000,100100000)
            self.assertEqual(db.execute('SELECT count(*) FROM spectral_observations').fetchone()[0],1)
            with db:ingest(db,'second',path,100000000,100100000)
            self.assertEqual(db.execute('SELECT observation_count FROM signals').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT count(*) FROM observation_fingerprints').fetchone()[0],2)
            db.close()
