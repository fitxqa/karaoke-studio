// Audio engine: plays the karaoke music plus optional guide vocals and duet partner with
// Web Audio, at any key and practice speed, and keeps the muted <video> in sync.
// Web Audio gives a precise clock for lyrics, scoring and recording alignment.

const LABELS = { music: 'karaoke music', vocals: 'guide vocals', male: 'male singer', female: 'female singer' };

export class Engine {
  constructor(video) {
    this.video = video;
    this.ctx = null;
    this.songId = null;
    this.key = 0;
    this.speed = 1;          // practice speed (pitch kept)
    this.offset = 0;         // song position when paused / when the current sources started
    this.startAt = 0;        // ctx time the current sources started
    this.playing = false;
    this.duration = 0;       // song length in song seconds
    this.tracks = { music: null, guide: null, partner: null };
    this.want = { guide: 0, partner: null, partnerVol: 0.9 };
    this.partnerVersion = 0; // bumped when duet parts are corrected
    this.sources = [];
    this.cache = new Map();
    this.onEnded = null;
    this.onLoading = null;   // (text|null)
  }

  get music() { return this.tracks.music; }

  ensureCtx() {
    if (this.ctx) return this.ctx;
    const ctx = new AudioContext({ latencyHint: 'interactive' });
    this.master = ctx.createGain();
    this.gains = {};
    for (const k of ['music', 'guide', 'partner']) {
      this.gains[k] = ctx.createGain();
      this.gains[k].connect(this.master);
    }
    this.gains.guide.gain.value = this.want.guide;
    this.gains.partner.gain.value = this.want.partnerVol;
    this.master.connect(ctx.destination);
    this.ctx = ctx;
    return ctx;
  }

  get outLatency() {
    const c = this.ctx;
    return c ? (c.outputLatency || 0) + (c.baseLatency || 0) : 0;
  }

  /** Song position you are hearing right now (seconds of the original song). */
  get time() {
    if (!this.playing) return this.offset;
    return Math.min(this.duration, this.offset + Math.max(0, this.ctx.currentTime - this.startAt - this.outLatency) * this.speed);
  }

  /** Song position that was being heard at audio-clock time `ctxTime`. */
  songTimeAtCtx(ctxTime) {
    return this.offset + (ctxTime - this.startAt - this.outLatency) * this.speed;
  }

  async _buffer(kind) {
    const pv = kind === 'male' || kind === 'female' ? this.partnerVersion : 0;
    const id = `${this.songId}:${kind}:${this.key}:${this.speed}:${pv}`;
    if (this.cache.has(id)) return this.cache.get(id);
    const label = LABELS[kind] || kind;
    const change = this.key !== 0 || this.speed !== 1;
    this.onLoading?.(change ? `Preparing ${label} (key ${this.key > 0 ? '+' : ''}${this.key}, speed ${Math.round(this.speed * 100)}%)… first time takes a few seconds`
      : `Loading ${label}…`);
    const res = await fetch(`/songs/${this.songId}/audio/${kind}.m4a?key=${this.key}&speed=${this.speed}&v=${pv}`);
    if (!res.ok) throw new Error(`Could not load ${label} (${res.status})`);
    const total = +res.headers.get('content-length') || 0;
    const reader = res.body.getReader();
    const parts = [];
    let got = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      parts.push(value);
      got += value.length;
      if (total) this.onLoading?.(`Loading ${label}… ${Math.round((got * 100) / total)}%`);
    }
    const bytes = new Uint8Array(got);
    let o = 0;
    for (const p of parts) { bytes.set(p, o); o += p.length; }
    this.onLoading?.('Preparing audio…');
    const buf = await this.ensureCtx().decodeAudioData(bytes.buffer);
    this.cache.set(id, buf);
    while (this.cache.size > 6) this.cache.delete(this.cache.keys().next().value); // memory
    return buf;
  }

  async _reload() {
    this.ensureCtx();
    try {
      this.tracks.music = await this._buffer('music');
      this.tracks.guide = this.want.guide > 0 ? await this._buffer('vocals') : null;
      this.tracks.partner = this.want.partner ? await this._buffer(this.want.partner) : null;
      this.duration = this.tracks.music.duration * this.speed;
    } finally {
      this.onLoading?.(null);
    }
  }

  async load(songId, key = 0, speed = 1) {
    this.songId = songId;
    this.key = key;
    this.speed = speed;
    await this._reload();
  }

  _stopSources() {
    for (const s of this.sources) { s.onended = null; try { s.stop(); } catch { /* not started */ } }
    this.sources = [];
  }

  _startSources() {
    this._stopSources();
    const ctx = this.ctx;
    this.startAt = ctx.currentTime + 0.05;
    const at = this.offset / this.speed; // position inside the (time-stretched) buffers
    let main = null;
    for (const k of ['music', 'guide', 'partner']) {
      const buf = this.tracks[k];
      if (!buf) continue;
      const s = ctx.createBufferSource();
      s.buffer = buf;
      s.connect(this.gains[k]);
      s.start(this.startAt, Math.min(at, buf.duration));
      this.sources.push(s);
      if (k === 'music') main = s;
    }
    main.onended = () => {
      if (!this.playing) return;
      this.playing = false;
      this.offset = this.duration;
      this._stopSources();
      this.onEnded?.();
    };
  }

  async play() {
    if (!this.tracks.music) return;
    await this.ensureCtx().resume();
    if (this.offset >= this.duration - 0.05) this.offset = 0;
    this._startSources();
    this.playing = true;
  }

  pause() {
    if (!this.playing) return;
    this.offset = this.time;
    this.playing = false;
    this._stopSources();
  }

  toggle() { return this.playing ? this.pause() : this.play(); }

  seek(t) {
    this.offset = Math.max(0, Math.min(this.duration || 0, t));
    if (this.playing) this._startSources();
    this.syncVideo(true);
  }

  /** Reload tracks after a change, keeping the position and play state. */
  async _change(apply) {
    const was = this.playing;
    const at = this.time;
    this.pause();
    apply();
    await this._reload();
    this.offset = Math.min(at, this.duration);
    if (was) await this.play();
  }

  setKey(k) { return this._change(() => { this.key = k; }); }
  setSpeed(s) { return this._change(() => { this.speed = s; }); }

  async setGuideVolume(v) {
    this.want.guide = v;
    if (this.ctx) this.gains.guide.gain.setTargetAtTime(v, this.ctx.currentTime, 0.05);
    if (v > 0 && !this.tracks.guide && this.songId) await this._change(() => {});
  }

  /** Duet partner: 'male' / 'female' singer track, or null for none. */
  async setPartner(kind) {
    this.want.partner = kind;
    if (!this.songId) return;
    await this._change(() => {});
  }

  setPartnerVolume(v) {
    this.want.partnerVol = v;
    if (this.ctx) this.gains.partner.gain.setTargetAtTime(v, this.ctx.currentTime, 0.05);
  }

  /** Duet parts were corrected: fetch the partner track again. */
  async refreshPartner() {
    this.partnerVersion++;
    if (this.want.partner && this.songId) await this._change(() => {});
  }

  /** Called every animation frame. */
  syncVideo(force = false) {
    const v = this.video;
    if (!v || v.readyState < 2) return;
    const t = this.time;
    const drift = v.currentTime - t;
    const base = this.speed;
    if (this.playing) {
      if (v.paused) v.play().catch(() => {});
      if (Math.abs(drift) > 0.3 || force) { v.currentTime = t; v.playbackRate = base; }
      else if (Math.abs(drift) > 0.04) v.playbackRate = base * (drift > 0 ? 0.96 : 1.04);
      else v.playbackRate = base;
    } else {
      if (!v.paused) v.pause();
      if (Math.abs(drift) > 0.08 || force) v.currentTime = t;
    }
  }

  stop() {
    this.pause();
    this.tracks = { music: null, guide: null, partner: null };
    this.want.partner = null;
    this.video.removeAttribute('src');
    this.video.load();
  }
}
