class Pcm16Capture extends AudioWorkletProcessor {
    constructor() {
      super();
      this.ratio = sampleRate / 16000;   // e.g. 48000/16000 = 3 input samples per output
      this.t = 0; this.sum = 0; this.count = 0;
      this.frame = new Int16Array(640); this.n = 0;
    }
    process(inputs) {
      const ch = inputs[0] && inputs[0][0];
      if (!ch) return true;
      for (let i = 0; i < ch.length; i++) {
        this.sum += ch[i]; this.count++; this.t += 1;
        if (this.t >= this.ratio) {               // enough input for one 16 kHz sample
          this.t -= this.ratio;
          const s = Math.max(-1, Math.min(1, this.sum / this.count));
          this.sum = 0; this.count = 0;
          this.frame[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
          if (this.n === 640) {
            this.port.postMessage(this.frame.buffer, [this.frame.buffer]);
            this.frame = new Int16Array(640); this.n = 0;
          }
        }
      }
      return true;
    }
  }
  registerProcessor("pcm16-capture", Pcm16Capture);
