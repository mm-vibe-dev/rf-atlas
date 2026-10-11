/* Bounded playback timing and explicit gap accounting; no RF or network I/O. */
(function(root){
 'use strict';
 class AudioClock {
  constructor(){this.reset();}
  reset(){this.end=0;this.target=.16;this.packets=0;this.underruns=0;this.gapSeconds=0;this.discardedSeconds=0;}
  schedule(now,duration){
   if(!Number.isFinite(now)||!Number.isFinite(duration)||duration<=0||duration>.31)throw Error('Invalid PCM timing');
   let gap=0;
   if(this.end<now){
    if(this.packets){gap=now-this.end;this.underruns++;this.gapSeconds+=gap;}
    this.end=now+this.target;
   }
   // Keep interactive latency bounded. Explicitly account for discarded PCM.
   if(this.end>now+.5){this.discardedSeconds+=duration;return {drop:true,gap_s:gap};}
   const start=this.end;this.end+=duration;this.packets++;
   return {drop:false,start,gap_s:gap};
  }
 }
 root.RFAtlasAudioClock=AudioClock;
 if(typeof module!=='undefined')module.exports=AudioClock;
})(typeof window==='undefined'?globalThis:window);
