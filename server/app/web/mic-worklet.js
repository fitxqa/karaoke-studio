// Captures raw microphone samples with the exact audio-clock frame they were recorded at.
class MicCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buf = new Float32Array(2048);
    this.n = 0;
    this.startFrame = 0;
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) {
      if (this.n === 0) this.startFrame = currentFrame;
      for (let i = 0; i < ch.length; i++) {
        this.buf[this.n++] = ch[i];
        if (this.n === this.buf.length) {
          this.port.postMessage({ frame: this.startFrame, data: this.buf }, [this.buf.buffer]);
          this.buf = new Float32Array(2048);
          this.n = 0;
          this.startFrame = currentFrame + i + 1;
        }
      }
    }
    return true;
  }
}
registerProcessor('mic-capture', MicCapture);
