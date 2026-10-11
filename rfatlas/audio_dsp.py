"""Causal rational polyphase audio conversion with exact streaming phase."""
import math
import numpy as np
from scipy import signal


class StreamingResampler:
    def __init__(self,taps,up,down):
        if type(up) is not int or type(down) is not int or not 1<=up<=256 or not 1<=down<=256:raise ValueError('Invalid rational rate')
        h=np.asarray(taps,dtype=float)
        if h.ndim!=1 or not 1<=len(h)<=8192 or not np.isfinite(h).all():raise ValueError('Invalid resampling FIR')
        self.taps=h.copy();self.up=up;self.down=down;self.phase=0
        self.keep=max(1,math.ceil((len(h)-1)/up));self.history=np.zeros(self.keep)
        self.filters={}

    def process(self,audio):
        x=np.asarray(audio,dtype=float)
        if x.ndim!=1 or len(x)>1_000_000 or not np.isfinite(x).all():raise ValueError('Invalid audio block')
        if not len(x):return np.empty(0)
        begin=(-self.phase)%self.down
        start=self.keep*self.up+begin
        delay=(-start)%self.down
        # Shift the FIR by < down samples to align upfirdn's output grid with
        # the original causal convolution grid. History supplies its full tail.
        if delay not in self.filters:self.filters[delay]=np.r_[np.zeros(delay),self.taps]
        combined=np.r_[self.history,x]
        y=signal.upfirdn(self.filters[delay],combined,up=self.up,down=self.down)
        first=(start+delay)//self.down
        count=max(0,(len(x)*self.up-begin+self.down-1)//self.down)
        self.history=combined[-self.keep:].copy();self.phase=(self.phase+len(x)*self.up)%self.down
        return y[first:first+count]
