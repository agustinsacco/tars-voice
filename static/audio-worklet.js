class TarsCaptureProcessor extends AudioWorkletProcessor {
  constructor(){super();this.input=[];this.position=0;this.ratio=sampleRate/16000}
  process(inputs){
    const channel=inputs[0]?.[0];if(!channel)return true;
    let sum=0;for(const sample of channel){sum+=sample*sample;this.input.push(sample)}
    const output=[];
    while(this.position+this.ratio<=this.input.length){
      const start=Math.floor(this.position),end=Math.max(start+1,Math.floor(this.position+this.ratio));let value=0;
      for(let i=start;i<end;i++)value+=this.input[i]||0;value/=end-start;output.push(Math.max(-1,Math.min(1,value)));this.position+=this.ratio
    }
    const consumed=Math.floor(this.position);if(consumed){this.input.splice(0,consumed);this.position-=consumed}
    if(output.length){const pcm=new Int16Array(output.length);for(let i=0;i<output.length;i++)pcm[i]=output[i]<0?output[i]*32768:output[i]*32767;this.port.postMessage({pcm:pcm.buffer,rms:Math.sqrt(sum/channel.length)},[pcm.buffer])}
    return true
  }
}
registerProcessor('tars-capture',TarsCaptureProcessor);
