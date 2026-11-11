// Pitch detection (YIN), resampling and singing-session scoring.

export const SR16 = 16000;
const WINDOW = 1024;
const HOP = 320; // 20 ms

export class PitchDetector {
  constructor({ sampleRate = SR16, window = WINDOW, minHz = 70, maxHz = 1100 } = {}) {
    Object.assign(this, { sampleRate, window, minHz, maxHz, threshold: 0.15, silenceRms: 0.012 });
    this.diff = new Float64Array(window >> 1);
  }

  /** Float32Array of `window` samples -> MIDI note, or null when silent/unvoiced. */
  detect(frame) {
    let sum = 0;
    for (let i = 0; i < frame.length; i++) sum += frame[i] * frame[i];
    if (Math.sqrt(sum / frame.length) < this.silenceRms) return null;
    const half = this.window >> 1, d = this.diff;
    const maxTau = Math.min(half - 1, Math.floor(this.sampleRate / this.minHz));
    const minTau = Math.max(2, Math.floor(this.sampleRate / this.maxHz));
    for (let tau = 1; tau <= maxTau; tau++) {
      let s = 0;
      for (let i = 0; i < half; i++) { const x = frame[i] - frame[i + tau]; s += x * x; }
      d[tau] = s;
    }
    d[0] = 1;
    let run = 0;
    for (let tau = 1; tau <= maxTau; tau++) { run += d[tau]; d[tau] = run === 0 ? 1 : d[tau] * tau / run; }
    let est = -1;
    for (let tau = minTau; tau <= maxTau; tau++) {
      if (d[tau] < this.threshold) {
        while (tau + 1 <= maxTau && d[tau + 1] < d[tau]) tau++;
        est = tau;
        break;
      }
    }
    if (est < 0) return null;
    let better = est;
    if (est > 1 && est < maxTau) {
      const s0 = d[est - 1], s1 = d[est], s2 = d[est + 1], den = 2 * (2 * s1 - s2 - s0);
      if (den !== 0) better = est + (s2 - s0) / den;
    }
    const hz = this.sampleRate / better;
    if (hz < this.minHz || hz > this.maxHz) return null;
    return 69 + 12 * Math.log2(hz / 440);
  }
}

/** Signed distance in semitones, folding octaves (singing an octave lower still counts). */
export function pitchDiff(user, ref) {
  let d = (((user - ref) % 12) + 12) % 12;
  if (d > 6) d -= 12;
  return d;
}

export function grade(score) {
  return score >= 90 ? 'S' : score >= 80 ? 'A' : score >= 65 ? 'B' : score >= 50 ? 'C' : 'D';
}

/** Streaming linear resampler. */
export class Resampler {
  constructor(inRate, outRate) { this.step = inRate / outRate; this.pos = 0; this.prev = 0; }
  push(input) {
    const n = input.length, out = [];
    const x = (i) => (i === 0 ? this.prev : input[i - 1]);
    while (this.pos + 1 <= n) {
      const i = Math.floor(this.pos), f = this.pos - i;
      out.push(x(i) * (1 - f) + x(i + 1) * f);
      this.pos += this.step;
    }
    this.pos -= n;
    if (n) this.prev = input[n - 1];
    return out;
  }
}

/** Reference melody of the original singer. */
export class PitchTrack {
  constructor(hop = 0.02, midi = []) {
    this.hop = hop;
    this.midi = midi;
    const v = midi.filter((m) => m != null).sort((a, b) => a - b);
    this.median = v.length ? v[v.length >> 1] : 60;
  }
  at(t) {
    if (t < 0) return null;
    const i = Math.floor(t / this.hop);
    return i < this.midi.length ? this.midi[i] : null;
  }
}

/**
 * One take: records the mic through the shared AudioContext (sample-accurate timing),
 * detects your pitch live, scores it, and produces a WAV for mixing.
 */
export class SingSession {
  constructor({ engine, reference, semitones, delayMs, onPoint }) {
    Object.assign(this, { engine, reference, semitones, delay: delayMs / 1000, onPoint });
    this.points = [];
    this.chunks = [];
    this.firstFrame = null;
    this.refFrames = 0; this.earned = 0; this.perfect = 0; this.good = 0;
    this.detector = new PitchDetector();
    this.buf16 = [];
    this.buf16Start = 0; // global index (16 kHz) of buf16[0]
  }

  get score() { return this.refFrames ? Math.min(100, (100 * this.earned) / this.refFrames) : 0; }

  async start() {
    const ctx = this.engine.ctx;
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error('This browser blocks the microphone here. Open the app at http://localhost:8000 or over https://');
    }
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 },
    });
    if (!ctx._micModule) { await ctx.audioWorklet.addModule('mic-worklet.js'); ctx._micModule = true; }
    this.sr = ctx.sampleRate;
    this.resampler = new Resampler(this.sr, SR16);
    this.src = ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(ctx, 'mic-capture');
    this.sink = ctx.createGain();
    this.sink.gain.value = 0; // keeps the node running without playing the mic out loud
    this.src.connect(this.node).connect(this.sink).connect(ctx.destination);
    this.node.port.onmessage = (e) => this._chunk(e.data);
  }

  _chunk({ frame, data }) {
    if (this.firstFrame === null) {
      this.firstFrame = frame;
      // song position (s) of the first mic sample, corrected for mic/speaker delay
      this.songStart = this.engine.songTimeAtCtx(frame / this.sr) - this.delay;
    }
    this.chunks.push(data);
    const s16 = this.resampler.push(data);
    for (const v of s16) this.buf16.push(v);
    while (this.buf16.length >= WINDOW) {
      const frameData = Float32Array.from(this.buf16.slice(0, WINDOW));
      const t = this.songStart + (this.buf16Start + WINDOW / 2) / SR16;
      const midi = this.detector.detect(frameData);
      this.buf16.splice(0, HOP);
      this.buf16Start += HOP;
      this._score(t, midi);
      const p = { t, midi };
      this.points.push(p);
      this.onPoint?.(p);
    }
  }

  _score(t, user) {
    const ref = this.reference.at(t);
    if (ref == null) return;
    this.refFrames++;
    if (user == null) return;
    const d = Math.abs(pitchDiff(user, ref + this.semitones));
    if (d <= 0.75) { this.earned += 1; this.perfect++; }
    else if (d <= 1.75) { this.earned += 0.6; this.good++; }
    else if (d <= 2.5) this.earned += 0.2;
  }

  _teardown() {
    try { this.src?.disconnect(); this.node?.disconnect(); this.sink?.disconnect(); } catch { /* already gone */ }
    this.stream?.getTracks().forEach((t) => t.stop());
  }

  cancel() { this._teardown(); }

  /** Stop and return { wav: Blob|null, songStart } */
  stop() {
    this._teardown();
    const total = this.chunks.reduce((a, c) => a + c.length, 0);
    if (total < this.sr) return { wav: null, songStart: this.songStart ?? 0 };
    // 24 kHz mono is plenty for a voice and halves the upload
    const outRate = this.sr >= 44100 ? 24000 : this.sr;
    const rs = new Resampler(this.sr, outRate);
    const pcm = [];
    for (const c of this.chunks) for (const v of rs.push(c)) pcm.push(v);
    return { wav: encodeWav(pcm, outRate), songStart: Math.max(0, this.songStart) };
  }
}

export function encodeWav(samples, rate) {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buf);
  const str = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  str(0, 'RIFF'); v.setUint32(4, 36 + samples.length * 2, true); str(8, 'WAVE');
  str(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  str(36, 'data'); v.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buf], { type: 'audio/wav' });
}
