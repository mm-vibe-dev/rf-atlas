"""Optional small CPU neural experiment trained on RF Atlas synthetic data.

No weights are bundled or downloaded. Scores are explicitly uncalibrated.
"""
from pathlib import Path
import time
import zipfile
import math
import numpy as np
from scipy import signal
from .features import extract_features

LABELS = ('AM','NFM','WFM','OOK','2FSK','4FSK','BPSK','QPSK','CW','UNKNOWN')
SCHEMA = 1


def feature_vector(iq, sample_rate):
    f = extract_features(iq, sample_rate)
    x = np.asarray(iq, complex); envelope = abs(x)
    norm = x / max(float(np.sqrt(np.mean(abs(x)**2))), 1e-15)
    inst = np.angle(x[1:]*x[:-1].conj())
    stats = [f['peak_snr_db'], f['spectral_flatness'], f['envelope_cv'],
        f['low_envelope_fraction'], f['occupied_bandwidth_hz']/sample_rate,
        f['instantaneous_frequency_span_hz']/sample_rate, f['frequency_level_concentration'],
        len(f['frequency_level_candidates_hz']), f['phase_jump_fraction'], f['adc_rail_fraction'],
        *f['phase_order_moments'], float(np.mean(abs(norm)**4))]
    for lag in (1, 2, 4, 8, 16, 32):
        stats.append(float(abs(np.mean(norm[lag:]*norm[:-lag].conj()))))
    for width in (4, 16, 64):
        smooth = signal.convolve(envelope, np.ones(width)/width, mode='valid')
        stats.append(float(np.std(smooth)/max(np.mean(smooth), 1e-15)))
    stats.extend(float(v) for v in np.quantile(inst-np.median(inst), (.05,.25,.75,.95)))
    return np.nan_to_num(np.asarray(stats, float), nan=0, posinf=0, neginf=0)


def train(training_cases, model_path, max_seconds=60, epochs=400):
    """Stream bounded synthetic cases to a tiny feature table and train one MLP."""
    from .corpus import generate_case
    if not 0 < max_seconds <= 300 or not 1 <= epochs <= 2000:
        raise ValueError('Training budget must be bounded')
    if not training_cases or any(c['split'] != 'development' for c in training_cases):
        raise ValueError('Training accepts development split only')
    started = time.perf_counter()
    features=[]; labels=[]
    for c in training_cases:
        features.append(feature_vector(generate_case(c), c['sample_rate']))
        labels.append(LABELS.index(c['target']))
        if time.perf_counter()-started > max_seconds:
            raise TimeoutError('Feature extraction exceeded bounded training budget')
    X=np.asarray(features); mean=X.mean(0); scale=np.maximum(X.std(0), .0001)
    X=np.clip((X-mean)/scale, -8, 8); y=np.eye(len(LABELS))[labels]
    rng=np.random.default_rng(46819); hidden=40
    params=[rng.normal(0,.15,(X.shape[1],hidden)),np.zeros(hidden),rng.normal(0,.15,(hidden,len(LABELS))),np.zeros(len(LABELS))]
    first=[np.zeros_like(p) for p in params]; second=[np.zeros_like(p) for p in params]
    completed=0
    for step in range(1, epochs+1):
        w1,b1,w2,b2=params; h=np.tanh(X@w1+b1); logits=h@w2+b2
        probs=np.exp(logits-logits.max(1,keepdims=True));probs/=probs.sum(1,keepdims=True)
        d=(probs-y)/len(X); dh=(d@w2.T)*(1-h*h)
        grads=[X.T@dh+.0001*w1,dh.sum(0),h.T@d+.0001*w2,d.sum(0)]
        for i,g in enumerate(grads):
            first[i]=.9*first[i]+.1*g;second[i]=.999*second[i]+.001*g*g
            params[i]-=.008*(first[i]/(1-.9**step))/(np.sqrt(second[i]/(1-.999**step))+1e-8)
        completed=step
        if time.perf_counter()-started>max_seconds: break
    destination=Path(model_path);destination.parent.mkdir(parents=True,exist_ok=True)
    np.savez_compressed(destination, schema=np.array(SCHEMA), mean=mean, scale=scale,
        w1=params[0],b1=params[1],w2=params[2],b2=params[3], labels=np.array(LABELS),
        training_seeds=np.array([c['seed'] for c in training_cases]), rejection_score=np.array(.55),rejection_margin=np.array(.12))
    return {'examples':len(X),'epochs':completed,'seconds':time.perf_counter()-started,
        'model_bytes':destination.stat().st_size,'training_accuracy':float(np.mean(probs.argmax(1)==np.array(labels))),
        'feature_table_bytes':X.nbytes,'license':'MIT project-generated synthetic data and weights',
        'note':'Training accuracy is not generalization accuracy; scores are uncalibrated'}


def load_model(path):
    """Load non-executable arrays and validate sizes before matrix operations."""
    path=Path(path)
    if path.stat().st_size>5_000_000: raise ValueError('Model exceeds bounded size')
    try:
        with zipfile.ZipFile(path) as archive:
            if sum(i.file_size for i in archive.infolist())>5_000_000 or len(archive.infolist())>20:
                raise ValueError('Uncompressed model exceeds bounded size')
            # Bound declared array shapes before NumPy can allocate from an
            # untrusted NPY header with a tiny/truncated data payload.
            for entry in archive.infolist():
                if not entry.filename.endswith('.npy'):raise ValueError('Unsupported model archive member')
                with archive.open(entry) as member:
                    version=np.lib.format.read_magic(member)
                    reader={(1,0):np.lib.format.read_array_header_1_0,(2,0):np.lib.format.read_array_header_2_0}.get(version)
                    if reader is None:raise ValueError('Unsupported model array version')
                    shape,_,dtype=reader(member,max_header_size=10000)
                    if dtype.hasobject or len(shape)>3 or math.prod(shape)*dtype.itemsize>5_000_000:
                        raise ValueError('Unsafe model array declaration')
        with np.load(path,allow_pickle=False) as archive:
            data={k:archive[k] for k in archive.files}
    except (zipfile.BadZipFile, EOFError, KeyError) as error:
        raise ValueError('Malformed model archive') from error
    required={'schema','labels','mean','scale','w1','b1','w2','b2','rejection_score','rejection_margin'}
    if not required.issubset(data):raise ValueError('Missing model parameters')
    if data['schema'].shape!=() or data['schema'].dtype.kind not in 'iu' or int(data['schema'])!=SCHEMA or data['labels'].shape!=(10,) or tuple(data['labels'])!=LABELS:
        raise ValueError('Unsupported model schema or labels')
    if data['mean'].ndim!=1 or data['b1'].ndim!=1:raise ValueError('Invalid model dimensions')
    n=len(data['mean']);h=len(data['b1'])
    if n!=26 or not 1<=h<=128 or data['scale'].shape!=(n,) or data['w1'].shape!=(n,h) or data['w2'].shape!=(h,len(LABELS)) or data['b2'].shape!=(len(LABELS),):
        raise ValueError('Invalid model shapes')
    if any(data[k].dtype.kind not in 'fiu' or not np.isfinite(data[k]).all() for k in ('mean','scale','w1','b1','w2','b2')) or np.any(data['scale']<=0):
        raise ValueError('Invalid model numeric parameters')
    for key in ('rejection_score','rejection_margin'):
        if data[key].shape!=() or data[key].dtype.kind not in 'fiu' or not np.isfinite(data[key]) or not 0<=float(data[key])<=1:
            raise ValueError('Invalid model rejection parameter')
    return data


def classify(iq, sample_rate, model_path):
    """Explicit opt-in model inference. Missing/invalid models raise for adapter fallback."""
    model=load_model(model_path);v=feature_vector(iq,sample_rate)
    X=np.clip((v-model['mean'])/model['scale'],-8,8)
    logits=np.tanh(X@model['w1']+model['b1'])@model['w2']+model['b2']
    scores=np.exp(logits-logits.max());scores/=scores.sum()
    order=np.argsort(scores)[::-1];top,runner=order[:2]
    accepted=scores[top]>=float(model['rejection_score']) and scores[top]-scores[runner]>=float(model['rejection_margin'])
    f=extract_features(iq,sample_rate);detected=f['peak_snr_db']>=10 and f['spectral_flatness']<.65
    label=LABELS[top] if accepted and detected and f['adc_rail_fraction']<=.01 else 'UNKNOWN'
    return {'signal_detected':bool(detected),'signal_class':'UNKNOWN','modulation':label,
        'protocol':'UNKNOWN','protection':'UNKNOWN','confidence':float(scores[top]) if label!='UNKNOWN' else 0.,'raw_top_score':float(scores[top]),
        'confidence_kind':'UNCALIBRATED_MODEL_SCORE','basis':'EXPERIMENTAL_SYNTHETIC_MLP',
        'candidates':[{'modulation':LABELS[i],'score':float(scores[i])} for i in order[:3]],
        'evidence':['Small synthetic-only CPU neural experiment; distribution shift unvalidated'],
        'features':f,'model_schema':SCHEMA,'rejected':label=='UNKNOWN'}
