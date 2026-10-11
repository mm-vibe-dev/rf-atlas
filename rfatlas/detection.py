"""Adaptive spectral detection, independent of device/platform."""
import numpy as np
from .diagnostics import noise_model

def detect(low, step, values, margin=6):
    power = np.asarray(values, dtype=float)
    if len(power) < 9 or not np.isfinite(power).all() or step <= 0:
        raise ValueError('Invalid spectrum row')
    floor,threshold = noise_model(power,step,margin)
    residual = power - floor
    mask = power > threshold
    # Edge bins are unreliable. Center/DC candidates are retained with warnings.
    mask[:2] = False; mask[-2:] = False
    changes = np.diff(np.r_[False, mask, False].astype(int))
    results = []
    for a, b in zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1)):
        weights = 10 ** ((power[a:b] - power[a:b].max()) / 10)
        centers = low + (np.arange(a, b) + .5) * step
        results.append({'low': low + a * step, 'high': low + b * step, 'center': float(np.average(centers, weights=weights)), 'peak': float(residual[a:b].max()), 'average': float(residual[a:b].mean()), 'noise': float(np.median(floor[a:b])), 'dc': bool(a <= len(power)//2 < b)})
    return results


def detect_survey(low,step,values,margin=6):
    """Preserve narrow detector; add broad prominent energy candidates.

    A broad local rise can be interference or several channels. This adds RF
    characterization only, without service/modulation/identity assertions.
    """
    from scipy.signal import find_peaks,peak_widths
    power=np.asarray(values,float)
    results=detect(low,step,power,margin)
    indices,properties=find_peaks(power,prominence=max(9,margin*1.5),width=max(5,50000/step))
    if not len(indices):return results
    widths,_,lefts,rights=peak_widths(power,indices,rel_height=.5)
    for index,prominence,left,right in zip(indices,properties['prominences'],lefts,rights):
        a=max(2,int(np.floor(left)));b=min(len(power)-2,int(np.ceil(right))+1)
        if b-a<5 or a<=2 or b>=len(power)-2:continue
        start,end=low+a*step,low+b*step
        if any(p['low']<end and p['high']>start for p in results):continue
        weights=10**((power[a:b]-power[a:b].max())/10)
        results.append(dict(low=start,high=end,center=float(np.average(low+(np.arange(a,b)+.5)*step,weights=weights)),
                            peak=float(prominence),average=float(np.mean(power[a:b]-(power[index]-prominence))),noise=float(power[index]-prominence),
                            dc=bool(a<=len(power)//2<b),method='BROAD_PROMINENCE_CANDIDATE'))
    return sorted(results,key=lambda p:p['center'])
