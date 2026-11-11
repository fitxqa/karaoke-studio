// Audio engine: plays the karaoke track (and optional guide vocals) with Web Audio,
// and keeps the muted <video> in sync. Web Audio gives a precise clock for lyrics,
// scoring and recording alignment.

export class Engine {
  constructor(video) {
    this.video = video;
    this.ctx = null;
    this.songId = null;
    this.key = 0;
    this.guide = false;
    this.offset = 0;     // song position when paused / when the current sources started
    this.startAt = 0;    // ctx time the current sources started
    this.playing = false;
    this.duration = 0;
    this.music = null;   // AudioBuffer
    this.vocals = null;  // AudioBuffer (guide)
    this.sources = [];
    this.cache = new Map();
    this.onEnded = null;
    this.onLoading = null; // (text|null)
  }

  ensureCtx() {
    if (this.ctx) return this.ctx;
    const ctx = new AudioContext({ latencyHint: 'interactive' });
    this.master = ctx.createGain();
    this.musicGain = ctx.createGain();
    this.vocalGain = ctx.createGain();
    this.vocalGain.gain.value = 0;
    this.musicGain.connect(this.master);
    this.vocalGain.connect(this.master);
    this.master.connect(ctx.destination);
    this.ctx = ctx;
    return ctx;
  }

  get outLatency() {
    const c = this.ctx;
    return c ? (c.outputLatency || 0) + (c.baseLatency || 0) : 0;
  }

  /** Song position you are hearing right now (seconds). */
  get time() {
    if (!this.playing) return this.offset;
    return Math.min(this.duration, this.offset + Math.max(0, this.ctx.currentTime - this.startAt - this.outLatency));
  }

  /** Song position that was being heard at audio-clock time `ctxTime`. */
  songTimeAtCtx(ctxTime) {
    return this.offset + (ctxTime - this.startAt - this.outLatency);
  }

  async _buffer(kind, key) {
    const id = `${this.songId}:${kind}:${key}`;
    if (this.cache.has(id)) return this.cache.get(id);
    const label = kind === 'music' ? 'karaoke music' : 'guide vocals';
    this.onLoading?.(key === 0 ? `Loading ${label}…` : `Changing key to ${key > 0 ? '+' : ''}${key}… (first time takes a few seconds)`);
    const res = await fetch(`/songs/${this.songId}/audio/${kind}.m4a?key=${key}`);
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
    // keep memory in check: at most 4 decoded tracks
    while (this.cache.size > 4) this.cache.delete(this.cache.keys().next().value);
    return buf;
  }

  async load(songId, key = 0) {
    this.songId = songId;
    this.key = key;
    this.ensureCtx();
    try {
      this.music = await this._buffer('music', key);
      this.vocals = this.guide ? await this._buffer('vocals', key) : null;
      this.duration = this.music.duration;
    } finally {
      this.onLoading?.(null);
    }
  }

  _stopSources() {
    for (const s of this.sources) { s.onended = null; try { s.stop(); } catch { /* not started */ } }
    this.sources = [];
  }

  _startSources() {
    this._stopSources();
    const ctx = this.ctx;
    this.startAt = ctx.currentTime + 0.05;
    const mk = (buf, dest) => {
      const s = ctx.createBufferSource();
      s.buffer = buf;
      s.connect(dest);
      s.start(this.startAt, Math.min(this.offset, buf.duration));
      this.sources.push(s);
      return s;
    };
    const main = mk(this.music, this.musicGain);
    if (this.vocals) mk(this.vocals, this.vocalGain);
    main.onended = () => {
      if (!this.playing) return;
      this.playing = false;
      this.offset = this.duration;
      this._stopSources();
      this.onEnded?.();
    };
  }

  async play() {
    if (!this.music) return;
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

  async setKey(k) {
    const was = this.playing;
    const at = this.time;
    this.pause();
    await this.load(this.songId, k);
    this.offset = at;
    if (was) await this.play();
  }

  async setGuide(on) {
    this.guide = on;
    if (on && !this.vocals) {
      const was = this.playing;
      const at = this.time;
      this.pause();
      try {
        this.vocals = await this._buffer('vocals', this.key);
      } finally {
        this.onLoading?.(null);
      }
      this.offset = at;
      if (was) await this.play();
    }
    if (this.ctx) this.vocalGain.gain.setTargetAtTime(on ? 0.7 : 0, this.ctx.currentTime, 0.05);
  }

  /** Called every animation frame. */
  syncVideo(force = false) {
    const v = this.video;
    if (!v || v.readyState < 2) return;
    const t = this.time;
    const drift = v.currentTime - t;
    if (this.playing) {
      if (v.paused) v.play().catch(() => {});
      if (Math.abs(drift) > 0.3 || force) { v.currentTime = t; v.playbackRate = 1; }
      else if (Math.abs(drift) > 0.04) v.playbackRate = drift > 0 ? 0.96 : 1.04;
      else v.playbackRate = 1;
    } else {
      if (!v.paused) v.pause();
      if (Math.abs(drift) > 0.08 || force) v.currentTime = t;
    }
  }

  stop() {
    this.pause();
    this.music = null;
    this.vocals = null;
    this.guide = false;
    if (this.vocalGain) this.vocalGain.gain.value = 0;
    this.video.removeAttribute('src');
    this.video.load();
  }
}
