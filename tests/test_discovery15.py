"""Original test-only protocol transmit waveforms; never transmitted over RF."""
import binascii
import json
import os
from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from scipy.ndimage import gaussian_filter1d
from rfatlas.decoders import DecoderInput,run_decoder
from rfatlas.dmr import DMRDecoder,csbk_crc_valid,slot_type
from rfatlas.ais import AISDecoder,parsed_ais
from rfatlas.characterization import characterize
from rfatlas.annotations import save,read,validate
from rfatlas.storage import open_database
from rfatlas.hf_spectrum import spectrum_rows
from rfatlas.inventory import configure_regions,default_profiles,InventoryWorker
from rfatlas.coverage import accounting


def dmr_wave(corrupt=False, rate=250000):
    # Encoder uses the systematic parity equations, independently of decoder.
    data=bytes.fromhex('BE100300000000000000')
    data+=(binascii.crc_hqx(data,0)^0xffff^0xa5a5).to_bytes(2,'big')
    if corrupt:data=data[:-1]+bytes([data[-1]^0x55])
    bits=np.unpackbits(np.frombuffer(data,dtype=np.uint8))
    block=np.zeros((13,15),dtype=np.uint8);block[0,3:11]=bits[:8];block[1:9,:11]=bits[8:].reshape(8,11)
    row_equations=[[0,1,2,3,5,7,8],[1,2,3,4,6,8,9],[2,3,4,5,7,9,10],[0,1,2,4,6,7,10]]
    col_equations=[[0,1,3,5,6],[0,1,2,4,6,7],[0,1,2,3,5,7,8],[0,2,4,5,8]]
    for row in block[:9]:
        for k,indexes in enumerate(row_equations):row[11+k]=np.bitwise_xor.reduce(row[indexes])
    for column in block.T:
        for k,indexes in enumerate(col_equations):column[9+k]=np.bitwise_xor.reduce(column[indexes])
    deinterleaved=np.r_[0,block.reshape(-1)];payload=np.zeros(196,dtype=np.uint8)
    payload[np.arange(196)*181%196]=deinterleaved
    # Fixed independently checked CC7/CSBK slot word, generated polynomial C75.
    value=0x73<<11;rem=value
    for bit in range(18,10,-1):
        if rem&(1<<bit):rem^=0xc75<<(bit-11)
    word=value^rem;word=(word<<1)|(word.bit_count()&1)
    slot=np.array([int(b) for b in f'{word:020b}'],dtype=np.uint8)
    sync=np.unpackbits(np.frombuffer(bytes.fromhex('DFF57D75DF5D'),dtype=np.uint8))
    burst=np.r_[payload[:98],slot[:10],sync,slot[10:],payload[98:]]
    dibits=burst.reshape(-1,2)@np.array([2,1]);levels=np.array([1,3,-1,-3])[dibits]
    # Three independent bursts at arbitrary timing phase.
    rails=np.r_[np.zeros(17),levels,np.zeros(12),levels,np.zeros(12),levels,np.zeros(17)]
    index=np.minimum((np.arange(int(len(rails)*rate/4800))*4800/rate).astype(int),len(rails)-1)
    frequency=gaussian_filter1d(rails[index].astype(float),rate/4800*.12)*648
    iq=.5*np.exp(2j*np.pi*np.cumsum(frequency)/rate)
    rng=np.random.default_rng(71)
    return iq+.001*(rng.normal(size=len(iq))+1j*rng.normal(size=len(iq)))


def ais_frame(corrupt=False):
    bits=np.zeros(168,dtype=np.uint8)
    def put(start,length,value):bits[start:start+length]=[int(b) for b in f'{value:0{length}b}']
    put(0,6,1);put(8,30,123);put(38,4,15)
    data=np.packbits(bits).tobytes()
    # Independent CRC via forward binascii and explicit reflection.
    reflected=bytes(int(f'{b:08b}'[::-1],2) for b in data)
    crc=int(f'{binascii.crc_hqx(reflected,0xffff):016b}'[::-1],2)^0xffff
    return data+(crc^(1 if corrupt else 0)).to_bytes(2,'little')


def ais_wave(data,rate=250000):
    stuffed=[];ones=0
    for bit in np.unpackbits(np.frombuffer(data,dtype=np.uint8),bitorder='little'):
        stuffed.append(int(bit));ones=ones+1 if bit else 0
        if ones==5:stuffed.append(0);ones=0
    flag=[0,1,1,1,1,1,1,0];bits=flag*8+stuffed+flag*8
    level=0;rails=[]
    for bit in bits:
        if not bit:level^=1
        rails.append(1 if level else -1)
    index=np.minimum((np.arange(round(len(rails)*rate/9600))*9600/rate).astype(int),len(rails)-1)
    frequency=gaussian_filter1d(np.array(rails)[index].astype(float),rate/9600*.35)*2400
    iq=.5*np.exp(2j*np.pi*np.cumsum(frequency)/rate)
    return np.pad(iq,(200,200),constant_values=.5)


def wh24_wave(corrupt_crc=False,corrupt_sum=False):
    payload=bytearray.fromhex('24125a026432020300010000000000')
    dividend=int.from_bytes(payload,'big')<<8
    while dividend.bit_length()>=9:dividend^=0x131<<(dividend.bit_length()-9)
    payload.append(dividend^int(corrupt_crc));payload.append((sum(payload)^int(corrupt_sum))&255)
    bits=np.r_[np.unpackbits(np.frombuffer(b'\xaa'*5+b'\x2d\xd4'+payload,dtype=np.uint8)),np.zeros(3,dtype=np.uint8)]
    index=np.minimum((np.arange(round(len(bits)*14.5))/14.5).astype(int),len(bits)-1)
    frequency=np.where(bits[index],18000,-18000)
    iq=.6*np.exp(2j*np.pi*np.cumsum(frequency)/250000)
    return DecoderInput(np.r_[np.zeros(7500),iq,np.zeros(7500)],250000,433920000)


class Protocol15Tests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('RFATLAS_RTL433'),'Optional actual rtl_433 not configured')
    def test_wh24_actual_binary_integrity_and_inventory(self):
        from rfatlas.external_decoders import RTL433WH24Decoder
        from rfatlas.identification import DecoderRegistry,identify,store_identification
        registry=DecoderRegistry();registry.register(RTL433WH24Decoder(os.environ['RFATLAS_RTL433']))
        for crc,total in ((False,False),(True,False),(False,True)):
            result=identify(wh24_wave(crc,total),{},registry)
            self.assertEqual(result['protocol_verified'],not(crc or total))
            if not(crc or total):
                fields=result['attempts'][0]['frames'][0]['parsed_fields']
                self.assertEqual(fields['temperature_c'],21.2);self.assertEqual(fields['rain_mm'],.3)
                with tempfile.TemporaryDirectory() as folder,closing(open_database(Path(folder))) as db:
                    db.execute("INSERT INTO signals(signal_id) VALUES('toy')");db.commit()
                    store_identification(db,result,{'signal_id':'toy'},'synthetic-wh24',retain_identifiers=True)
                    entity=db.execute('SELECT identifier,latest_fields FROM entities').fetchone()
                    self.assertEqual(entity[0],'18');self.assertIn('rain_mm',json.loads(entity[1]))

    def test_sensor_output_boundary(self):
        from rfatlas.external_decoders import RTL433WH24Decoder
        event=dict(model='Fineoffset-WH24',id=18,mic='CRC',battery_ok=1,temperature_C=21.2,rain_mm=.3)
        with patch('rfatlas.external_decoders.shutil.which',return_value='trusted'):
            for value,expected in ((event,True),(dict(event,mic='CHECKSUM'),False),
                    (dict(event,rain_mm=-1),False),(dict(event,id=True),False),
                    (dict(event,model='Fineoffset-WH65B'),False),(dict(event,battery_ok=True),False)):
                decoder=RTL433WH24Decoder(runner=lambda *args:(json.dumps(value),''))
                self.assertEqual(run_decoder(decoder,wh24_wave())['protocol_verified'],expected)

    def test_offset_dmr_is_not_tied_to_known_frequency(self):
        base=dmr_wave();n=np.arange(len(base))
        for offset in (-10000,10000):
            result=run_decoder(DMRDecoder(),DecoderInput(base*np.exp(2j*np.pi*offset*n/250000),250000,20000000))
            self.assertTrue(result['protocol_verified'])
            self.assertAlmostEqual(result['frames'][0]['parsed_fields']['channel_offset_hz'],offset,delta=300)

    def test_legacy_profile_catalog_preserves_operator_policy(self):
        from rfatlas.inventory import load_profiles
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'profiles.toml'
            original=default_profiles()[:18]
            original[0]['capture_allowed']=False
            text='\n'.join('[[regions]]\n'+'\n'.join(k+' = '+json.dumps(v) for k,v in p.items()) for p in original)
            path.write_text(text,encoding='utf8')
            loaded=load_profiles(path)
            self.assertEqual(loaded[:18],original)
            self.assertEqual(len(loaded),28)
            self.assertTrue(all(not p['enabled'] for p in loaded[18:]))
            self.assertEqual(path.read_text(encoding='utf8'),text)
        self.assertTrue(all(p['capture_allowed'] for p in default_profiles()[:4]))

    def test_full_scheduling_fairness_and_failed_coverage(self):
        with tempfile.TemporaryDirectory() as directory,closing(open_database(Path(directory))) as db:
            profiles=default_profiles()[:4];configure_regions(db,profiles)
            worker=InventoryWorker(db,None,None,None,None)
            first=worker._due_region(1)[0]
            db.execute("UPDATE inventory_regions SET visit_count=1,valid_count=1,stage='fine' WHERE region_id=?",(first['region_id'],));db.commit()
            second=worker._due_region(1)[0]
            self.assertNotEqual(first['region_id'],second['region_id'])
            result=accounting(db)
            self.assertEqual(result['configured_intervals_hz'],[[500000,1766000000]])
            self.assertEqual(result['unique_valid_hz'],2000000)
            self.assertEqual(result['successful_coverage_cycles'],0)
            # Resume a fresh worker: the persisted least-visited choice survives.
            self.assertEqual(InventoryWorker(db,None,None,None,None)._due_region(1)[0]['region_id'],second['region_id'])

    def test_dsd_file_only_diagnostic_cleanup(self):
        import wave
        from rfatlas.dsd import DSDFMEDiagnostic
        observed=[]
        def runner(argv,timeout,**kwargs):
            self.assertNotIn('rtl',argv);self.assertEqual(argv[argv.index('-o')+1],'null')
            self.assertNotIn('-T',argv);path=Path(argv[argv.index('-i')+1]);observed.append(path)
            self.assertEqual(Path(kwargs['cwd']),path.parent)
            with wave.open(str(path)) as stream:
                self.assertEqual((stream.getframerate(),stream.getnchannels(),stream.getsampwidth()),(48000,1,2))
            return '', 'DMR Sync Color Code 7'
        with patch('rfatlas.dsd.shutil.which',return_value='trusted'):
            result=run_decoder(DSDFMEDiagnostic(runner=runner),DecoderInput(dmr_wave(),250000,400000000))
        self.assertFalse(result['protocol_verified']);self.assertEqual(result['outcome'],'DIAGNOSTIC_UNVERIFIED')
        self.assertEqual(result['frames'],[]);self.assertFalse(observed[0].exists())

    def test_generic_decoder_routing_and_unavailable_explanations(self):
        from rfatlas.identification import default_registry,select_candidates
        recording=DecoderInput(dmr_wave(),250000,20000000)
        candidates=select_candidates(default_registry('missing-optional-tool'),recording,{'modulation':'4FSK'})
        self.assertTrue(next(c for c in candidates if c['decoder']=='native-dmr-bs-csbk')['eligible'])
        self.assertTrue(next(c for c in candidates if c['decoder']=='native-ais-position')['eligible'])
        self.assertIn('decoder unavailable on this platform',next(c for c in candidates if c['decoder']=='rtl_433-wh24')['reasons'])

    def test_modulation_evidence_outweighs_broad_band_hints(self):
        from tests.test_packet import afsk_iq,frame
        from rfatlas.identification import default_registry,identify
        recording=DecoderInput(afsk_iq(frame(),rate=250000),250000,450000000)
        registry=default_registry('missing-optional-tool')
        # Treat all adapters as selectable to exercise the real four-attempt
        # budget even on hosts without the optional sensor executable.
        with patch.object(registry,'available',return_value=True):
            result=identify(recording,dict(modulation='2FSK',features=dict(occupied_bandwidth_hz=15000)),registry=registry)
        self.assertEqual(result['protocol'],'AX.25')
        self.assertLessEqual(len(result['attempts']),4)

    def test_dmr_waveform_and_corrupt_crc(self):
        from rfatlas.identification import identify,default_registry
        registry=default_registry()
        registry.decoders={DMRDecoder.name:DMRDecoder()}
        identified=identify(DecoderInput(dmr_wave(),250000,400000000),dict(modulation='UNKNOWN'),registry=registry)
        self.assertEqual(identified['characterization']['modulation'],'4FSK')
        self.assertEqual(identified['analysis']['modulation'],'UNKNOWN')
        for corrupt in (False,True):
            result=run_decoder(DMRDecoder(),DecoderInput(dmr_wave(corrupt),250000,400000000))
            self.assertEqual(result['protocol_verified'],not corrupt)
            if not corrupt:
                fields=result['frames'][0]['parsed_fields']
                self.assertEqual(fields['color_code'],7);self.assertEqual(fields['rest_channel'],3)
                self.assertNotIn('timeslot',fields);self.assertNotIn('lcn',fields)

    def test_crc_known_check_and_slot_rejection(self):
        self.assertTrue(csbk_crc_valid(bytes.fromhex('BE100300000000000000B18A')))
        self.assertIsNone(slot_type(np.array([int(b) for b in f'{0x0000F:020b}'])))

    def test_ais_modem_crc_and_type(self):
        for corrupt in (False,True):
            result=run_decoder(AISDecoder(),DecoderInput(ais_wave(ais_frame(corrupt)),250000,161975000))
            self.assertEqual(result['protocol_verified'],not corrupt)
            if not corrupt:self.assertEqual(result['frames'][0]['parsed_fields']['mmsi'],'000000123')
        with self.assertRaises(ValueError):parsed_ais(ais_frame(True))

    def test_noise_does_not_verify(self):
        rng=np.random.default_rng(33)
        for decoder in (DMRDecoder(),AISDecoder()):
            iq=.1*(rng.normal(size=100000)+1j*rng.normal(size=100000))
            self.assertFalse(run_decoder(decoder,DecoderInput(iq,250000,200000000))['protocol_verified'])

    def test_hierarchy_abstains_and_rds_is_mixed(self):
        self.assertEqual(characterize({})['signal_type'],'UNKNOWN')
        self.assertEqual(characterize({},['RDS'])['signal_type'],'ANALOG')
        self.assertEqual(characterize({},['DMR'])['signal_type'],'DIGITAL')
        self.assertEqual(characterize(dict(signal_detected=True,signal_class='ANALOG',modulation='AM'))['signal_type'],'ANALOG')

    def test_hf_capture_chunks_and_invalid_streams(self):
        from rfatlas.hf_spectrum import acquire
        class Backend:
            selector='test'
            def __init__(self,pattern):self.pattern=pattern;self.counts=[];self.paths=[]
            def tool(self,name):return name
            def run(self,args,timeout,diagnostic):
                count=int(args[args.index('-n')+1]);path=Path(args[-1])
                path.write_bytes((self.pattern*((count*2+len(self.pattern)-1)//len(self.pattern)))[:count*2])
                self.counts.append(count);self.paths.append(path)
                return '', 'Exact sample rate is: 2400000'
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'scan.csv';backend=Backend(bytes([127,128,129,130]))
            with patch('rfatlas.hf_spectrum.spectrum_rows',return_value=[(500000,2500000,1000,np.array([-90.,-80.]))]):
                acquire(backend,output,500000,2500000,1000,8.1,'15.7')
            self.assertEqual(len(backend.counts),4)
            self.assertLessEqual(max(backend.counts)*2,39840000)
            self.assertTrue(all(not path.exists() for path in backend.paths))
            command=json.loads(output.with_suffix('.command.json').read_text())
            self.assertAlmostEqual(sum(v['used_complex_samples'] for v in command['quality'])/2400000,16.2)
            with self.assertRaisesRegex(ValueError,'Constant'):
                acquire(Backend(bytes([128,128])),output,500000,2500000,1000,.01,'15.7')
            from rfatlas.measurement_errors import SpectrumMeasurementError
            with self.assertRaises(SpectrumMeasurementError):
                acquire(Backend(bytes([0,128,255,129])),output,500000,2500000,1000,.01,'15.7')
            self.assertEqual(json.loads(output.with_suffix('.rejected.json').read_text())['reason'],'ADC_CLIPPING')

    def test_nonfinite_survey_fallback_preserves_failed_measurement(self):
        import csv
        from types import SimpleNamespace
        from rfatlas.survey import survey
        from rfatlas import legacy
        from rfatlas.platforms import DeviceLock
        class Backend:
            selector='test';ingest=staticmethod(legacy.ingest)
            def tool(self,name):return name
            def lock(self):return DeviceLock(root/'locks')
            def run(self,args,timeout,diagnostic):
                Path(args[-1]).write_text('2026-01-01,12:00:00,99950000,102050000,25000,100,'+','.join(['nan']+['-60']*83),encoding='utf8')
                Path(diagnostic).write_text(json.dumps(dict(command=['rtl_power'],started='2026-01-01T12:00:00+00:00')),encoding='utf8')
                return '', ''
        def replacement(backend,path,low,high,step,duration,gain):
            with path.open('w',newline='',encoding='utf8') as stream:csv.writer(stream).writerow(['2026-01-01','12:00:01+00:00',low,high,step,100,*([-60]*80)])
            path.with_suffix('.command.json').write_text(json.dumps(dict(backend='V4_RF_FLOAT_WELCH',stderr='Exact sample rate is: 2400000',settings=dict(duration_s=duration))),encoding='utf8')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'data/spectra').mkdir(parents=True)
            with closing(open_database(root)) as db,patch('rfatlas.hf_spectrum.acquire',side_effect=replacement) as retry:
                args=SimpleNamespace(start=100000000,stop=102000000,profile='quick',antenna='test',new=True,integration=1,duration=3,gain='15.7',margin=6)
                run=survey(db,args,root,Backend())
                self.assertEqual(retry.call_count,1)
                accepted=Path(db.execute('SELECT raw_path FROM scan_regions WHERE run_id=?',(run,)).fetchone()[0])
                metadata=json.loads(accepted.with_suffix('.command.json').read_text())
                rejected=Path(metadata['fallback']['rejected_spectrum'])
                self.assertIn('nan',rejected.read_text())
                self.assertTrue(rejected.with_suffix('.command.json').is_file())
                self.assertTrue(json.loads(rejected.with_suffix('.parse.json').read_text())['rejected_rows'])
                self.assertEqual(metadata['backend'],'V4_RF_FLOAT_WELCH')
                self.assertEqual(db.execute('SELECT status FROM survey_runs WHERE run_id=?',(run,)).fetchone()[0],'complete')
                profile=dict(default_profiles()[0],id='fallback-test',start_hz=100000000,stop_hz=102000000,capture_allowed=False)
                configure_regions(db,[profile]);worker=InventoryWorker(db,None,lambda region,settings:run,None,None)
                worker.step_scan(worker._due_region(1)[0])
                quality=json.loads(db.execute('SELECT quality FROM inventory_regions').fetchone()[0])
                self.assertEqual(quality['integration_s'],3)
                self.assertEqual(quality['requested_integration_s'],1)
                self.assertEqual(quality['acquisition_backends'],['V4_RF_FLOAT_WELCH'])
                self.assertEqual(len(quality['fallback_rejections']),1)

    def test_hf_fft_crop_and_dc_exclusion(self):
        rate=2400000;n=np.arange(240000)
        iq=.3*np.exp(2j*np.pi*300000*n/rate)+.2
        rows=spectrum_rows(iq,rate,2000000,5000)
        for low,high,step,powers in rows:
            self.assertTrue(np.isfinite(powers).all())
            self.assertLessEqual(max(abs(low-2000000),abs(high-2000000)),rate*.41)
            self.assertFalse(low<2000000<high)
        row=rows[1];peak=row[0]+(np.argmax(row[3])+.5)*row[2]
        self.assertLess(abs(peak-2300000),row[2])

    def test_annotations_restart_private_and_not_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            with closing(open_database(root)) as db:
                db.execute("INSERT INTO signals(signal_id) VALUES('toy')");db.commit()
                value=dict(name='Test Δ',notes='<script>text only</script>',tags=['test'],favorite=True)
                save(db,'toy',value)
            with closing(open_database(root)) as db:
                self.assertEqual(read(db,'toy'),value)
                self.assertEqual(db.execute("SELECT protocol FROM signals WHERE signal_id='toy'").fetchone()[0],'UNKNOWN')
                with self.assertRaises(ValueError):save(db,'missing',value)
        for bad in (dict(name='',notes='',tags=[],favorite=1),dict(name='',notes='x'*4001,tags=[],favorite=False)):
            with self.assertRaises(ValueError):validate(bad)


class AnnotationHTTPTests(unittest.TestCase):
    from tests.test_runtime_http import RuntimeHTTPTests as _HTTP
    setUp=_HTTP.setUp
    close_server=_HTTP.close_server
    request=_HTTP.request

    def test_verified_inventory_interpretation_preserves_stored_hypothesis(self):
        with closing(open_database(self.root)) as db:
            db.execute("INSERT INTO signals(signal_id,protocol,signal_class,modulation) VALUES('dmr','DMR','ANALOG','AM')");db.commit()
        detail=self.request('GET','/api/signals/dmr')[1]['signal']
        self.assertEqual((detail['signal_class'],detail['modulation']),('DIGITAL','4FSK'))
        self.assertEqual(detail['stored_characterization'],dict(signal_class='ANALOG',modulation='AM'))
        self.assertEqual(detail['rf_family_basis'],'VERIFIED_PROTOCOL_INTERPRETATION')
        self.assertEqual(self.request('GET','/api/signals?signal_class=DIGITAL&modulation=4FSK')[1]['total'],1)
        with closing(open_database(self.root)) as db:
            self.assertEqual(db.execute("SELECT signal_class,modulation FROM signals WHERE signal_id='dmr'").fetchone(),('ANALOG','AM'))

    def test_annotation_csrf_and_family_filters(self):
        with closing(open_database(self.root)) as db:
            db.execute("INSERT INTO signals(signal_id,center_frequency_hz,protocol) VALUES('toy',100000000,'RDS')");db.commit()
        value=dict(name='Test Δ',notes='<script>plain text</script>',tags=['favorite'],favorite=True)
        path='/api/signals/toy/annotations'
        self.assertEqual(self.request('POST',path,value,headers={'X-RFAtlas-Token':''})[0],403)
        self.assertEqual(self.request('POST',path,value)[0],200)
        detail=self.request('GET','/api/signals/toy')[1]
        self.assertEqual(detail['annotations'],value);self.assertEqual(detail['signal']['protocol'],'RDS')
        self.assertEqual(detail['signal']['signal_class'],'ANALOG')
        self.assertEqual(detail['signal']['modulation'],'WFM')
        self.assertEqual(detail['signal']['metadata_protocols'],['RDS'])
        data=self.request('GET','/api/signals?family=broadcast&signal_class=ANALOG')[1]
        self.assertEqual(data['total'],1);self.assertEqual(data['signals'][0]['annotations'],value)
        self.assertEqual(self.request('GET','/api/signals?modulation=WFM')[1]['total'],1)
        self.assertEqual(self.request('GET','/api/signals?q=Test&sort=favorite')[1]['total'],1)
        self.assertEqual(self.request('GET','/api/signals?q=100.000000')[1]['total'],1)
        self.assertEqual(self.request('GET','/api/signals?sort=invalid')[0],400)
        self.assertEqual(self.request('GET','/api/signals?family=marine')[1]['total'],0)
        self.assertEqual(self.request('GET','/api/signals?family=not-a-family')[0],400)
        self.assertEqual(self.request('POST',path,dict(value,favorite=1))[0],400)
        self.assertEqual(self.request('POST','/api/signals/absent/annotations',value)[0],400)


if __name__=='__main__':unittest.main()
