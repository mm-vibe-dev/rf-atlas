"""Phase 1.1 acquisition diagnostics; no protocol classification."""
import csv
import json
from pathlib import Path
import numpy as np
from scipy.ndimage import median_filter
from scipy import signal


def atomic_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf8')
    temp.replace(path)


def noise_model(values, step, margin=6):
    power = np.asarray(values, float)
    if len(power) < 9 or not np.isfinite(power).all() or not np.isfinite(step) or step <= 0:
        raise ValueError('Invalid spectrum row')
    # A minimum 50 kHz context keeps narrow 12.5 kHz channels out of
    # their own noise-floor estimate, even on a 200 kHz validation scan.
    window = min(len(power) if len(power)%2 else len(power)-1,
                 max(9, min(501, int(50000/step) | 1), (len(power)//8)|1))
    floor = median_filter(power, size=window, mode='reflect')
    spread = median_filter(np.abs(power-floor), size=window, mode='reflect')*1.4826
    return floor, floor + np.maximum(float(margin), 4*spread)


def read_spectra(path):
    """Reject complete rows with invalid data; preserve every rejection reason."""
    spectra=[]
    report={'path':str(path),'rows':0,'valid_bins':0,'rejected_bins':0,'rejected_rows':[], 'duplicate_tail_bins':0,'row_details':[]}
    with Path(path).open(newline='',encoding='utf8') as file:
        for index,row in enumerate(csv.reader(file),1):
            report['rows']+=1; reasons=[]; values=[]
            for i,token in enumerate(row[6:]):
                try: value=float(token)
                except ValueError: value=float('nan')
                if not np.isfinite(value):
                    reasons.append({'bin':i,'token':token.strip(),'reason':'nonfinite or malformed power'})
                    report['rejected_bins']+=1
                else: report['valid_bins']+=1
                values.append(value)
            try:
                if len(row)<15: raise ValueError('Too few columns')
                low,high,step=map(float,row[2:5]);samples=float(row[5])
                if not np.isfinite([low,high,step,samples]).all() or step<=0 or high<=low or samples<=0:
                    raise ValueError('Invalid frequency/step/sample metadata')
                expected=round((high-low)/step)
                if abs(len(values)-expected)>3: raise ValueError('Frequency/bin count mismatch')
                if len(values)<expected: raise ValueError('Missing power bins')
                detail={'row':index,'header_low_hz':low,'header_high_hz':high,'step_hz':step,'samples':samples,'raw_bins':len(values),'expected_header_bins':expected}
                # rtl_power csv_dbm emits the final FFT bin twice. Cropping
                # rounds headers separately; the remaining extra bins are real.
                if len(values)>expected and len(values)>1 and values[-1]==values[-2]:
                    values=values[:-1];report['duplicate_tail_bins']+=1
                    detail['duplicate_last_bin_removed']=True
                if detail.get('duplicate_last_bin_removed') or len(values)>expected:
                    centers=(low+high)/2+(np.arange(len(values))-len(values)/2)*step
                else:
                    centers=low+(np.arange(len(values))+.5)*step
                detail.update(first_center_hz=float(centers[0]),last_center_hz=float(centers[-1]),unique_bins=len(values))
                report['row_details'].append(detail)
                if reasons: raise ValueError('Invalid power bins: entire row rejected')
                spectra.append({'observed':row[0].strip()+' '+row[1].strip(),'low':float(centers[0]-step/2),'high':float(centers[-1]+step/2),'step':step,'power':np.array(values),'centers':centers})
            except (ValueError,IndexError) as error:
                report['rejected_rows'].append({'row':index,'reason':str(error),'bins':reasons})
    if not report['rows']: report['rejected_rows'].append({'row':0,'reason':'Empty CSV','bins':[]})
    return spectra,report


def spectrum_metrics(spectrum,target,margin=6):
    from .detection import detect
    power=spectrum['power'];centers=spectrum['centers'];step=spectrum['step']
    floor,threshold=noise_model(power,step,margin)
    nearest=int(np.argmin(abs(centers-target)))
    peaks=detect(spectrum['low'],step,power,margin)
    matched=[p for p in peaks if p['low'] <= target+7500 and p['high']>=target-7500]
    neighbors=[{'center_hz':float(centers[i]),'error_hz':float(centers[i]-target),'power_db':float(power[i]),'noise_db':float(floor[i]),'snr_db':float(power[i]-floor[i]),'threshold_db':float(threshold[i]),'threshold_excess_db':float(threshold[i]-floor[i])} for i in range(max(0,nearest-3),min(len(power),nearest+4))]
    return {'nearest':neighbors[min(3,nearest)],'neighbors':neighbors,'candidate_count':len(peaks),'target_detected':bool(matched),'other_candidates':len(peaks)-len(matched),'peaks':peaks}


def plot_spectrum(spectrum,path,target=None,margin=6,title='Detector diagnostics'):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from .detection import detect
    floor,threshold=noise_model(spectrum['power'],spectrum['step'],margin)
    fig,ax=plt.subplots(figsize=(12,5));x=spectrum['centers']/1e6
    ax.plot(x,spectrum['power'],label='Measured power',lw=.9)
    ax.plot(x,floor,label='Local noise estimate');ax.plot(x,threshold,label='Adaptive threshold')
    for p in detect(spectrum['low'],spectrum['step'],spectrum['power'],margin): ax.axvspan(p['low']/1e6,p['high']/1e6,color='green',alpha=.18)
    if target is not None: ax.axvline(target/1e6,color='red',ls='--',label='Validation marker')
    ax.set(xlabel='Frequency (MHz)',ylabel='Relative instrument power (dB)',title=title);ax.legend();ax.grid(alpha=.2)
    Path(path).parent.mkdir(parents=True,exist_ok=True);fig.tight_layout();fig.savefig(path,dpi=140);plt.close(fig)


def survey_diagnostics(path,target,output_dir,margin=6):
    spectra,parse=read_spectra(path)
    result={'parse':parse,'target_hz':target,'margin_db':margin,'spectra':[spectrum_metrics(s,target,margin) for s in spectra]}
    command_path=Path(path).with_suffix('.command.json')
    if command_path.exists(): result['acquisition']=json.loads(command_path.read_text(encoding='utf8'))
    # Evaluate unchanged adaptive dispersion criterion at each margin.
    result['sensitivity']=[{'margin_db':m,'target_detection_spectra':sum(spectrum_metrics(s,target,m)['target_detected'] for s in spectra),'spectra':len(spectra),'other_candidate_observations':sum(spectrum_metrics(s,target,m)['other_candidates'] for s in spectra)} for m in [3,6,9,12]]
    destination=Path(output_dir)/Path(path).stem
    atomic_json(destination.with_suffix('.json'),result)
    if spectra:
        best=max(range(len(spectra)),key=lambda i:result['spectra'][i]['nearest']['snr_db'])
        plot_spectrum(spectra[best],destination.with_suffix('.png'),target,margin,title=f'{Path(path).stem}: highest nearest-bin excess')
    return result


def analyze_iq(path,sample_rate,center_hz,target_hz):
    """Welch and short-time RF energy; never infer a protocol from a filename."""
    raw=np.fromfile(path,dtype=np.uint8)
    if len(raw)%2 or len(raw)<8192: raise ValueError('Incomplete cu8 capture')
    iq=(raw[0::2].astype(np.float32)-127.5)/127.5+1j*(raw[1::2].astype(np.float32)-127.5)/127.5
    total=float(10*np.log10(np.mean(np.abs(iq)**2)))
    dc=complex(iq.mean());iq-=dc
    nfft=4096
    f,t,psd=signal.spectrogram(iq,fs=sample_rate,nperseg=nfft,noverlap=nfft//2,return_onesided=False,mode='psd')
    f=np.fft.fftshift(f);psd=np.fft.fftshift(psd,axes=0)
    average=psd.mean(axis=1);power=10*np.log10(np.maximum(average,1e-30))
    step=sample_rate/nfft;centers=f+center_hz
    spectrum={'low':float(centers[0]-step/2),'high':float(centers[-1]+step/2),'step':step,'centers':centers,'power':power,'observed':'IQ'}
    metrics=spectrum_metrics(spectrum,target_hz)
    # Measure integrated channel excess against neighboring guard bands,
    # excluding the +/-500 Hz DC region to avoid claiming a DC spike as RF.
    offsets=centers-target_hz
    channel=(abs(offsets)<7500)&(abs(offsets)>500)
    guards=(abs(offsets)>20000)&(abs(offsets)<50000)
    noise=psd[guards].mean(axis=0);energy=psd[channel].mean(axis=0)
    excess=10*np.log10(np.maximum(energy,1e-30)/np.maximum(noise,1e-30))
    active=excess>6
    occupied_psd=psd[:,active].mean(axis=1) if active.any() else average
    excess_power=np.maximum(occupied_psd[abs(offsets)<20000]-np.median(occupied_psd[guards]),0)
    band_centers=centers[abs(offsets)<20000]
    cumulative=np.cumsum(excess_power)
    if cumulative[-1]>0:
        lo=band_centers[np.searchsorted(cumulative,.005*cumulative[-1])];hi=band_centers[min(len(cumulative)-1,np.searchsorted(cumulative,.995*cumulative[-1]))]
        bw=float(hi-lo+step);centroid=float(np.average(band_centers,weights=excess_power))
    else: bw=0.;centroid=None
    changes=np.diff(np.r_[False,active,False].astype(int));starts=np.flatnonzero(changes==1);ends=np.flatnonzero(changes==-1)
    dt=float(nfft/2/sample_rate)
    bursts=[{'start_s':float(t[a]),'duration_s':float((b-a)*dt)} for a,b in zip(starts,ends)]
    factor=max(1,round(1000/step));n=len(average)//factor
    grouped_power=10*np.log10(np.maximum(average[:n*factor].reshape(n,factor).mean(axis=1),1e-30))
    grouped_centers=centers[:n*factor].reshape(n,factor).mean(axis=1)
    grouped={'low':float(grouped_centers[0]-factor*step/2),'high':float(grouped_centers[-1]+factor*step/2),'step':factor*step,'power':grouped_power,'centers':grouped_centers}
    grouped_metrics=spectrum_metrics(grouped,target_hz)
    result={'path':str(path),'sample_rate':sample_rate,'center_hz':center_hz,'duration_s':len(iq)/sample_rate,'total_power_dbfs':total,'dc_offset_magnitude':abs(dc),'occupied_bandwidth_99pct_hz':bw,'bandwidth_method':'99% noise-subtracted +/-20kHz channel power in active frames; approximate, not a protocol label','spectral_centroid_hz':centroid,'channel_excess_median_db':float(np.median(excess)),'channel_excess_max_db':float(excess.max()),'active_fraction':float(active.mean()),'bursts':bursts,'rf_present':bool(np.mean(active)>.01 and bw>1500),'metrics':metrics,'survey_resolution_metrics':grouped_metrics,'classification':'UNKNOWN; no classifier in Phase 1.1'}
    return result,spectrum
