import { Engine } from './engine.js?v=1.4.0';
import { PitchTrack, SingSession, grade, pitchDiff } from './pitch.js?v=1.4.0';

const APP_VERSION = '1.4.0';
const KS = window.KS || { isApp: false, U: (p) => p, plugins: {} };
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (s) => { s = Math.max(0, Math.round(s || 0)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

// ------------------------------------------------------------------ settings

const store = {
  get(k, d) { try { const v = localStorage.getItem('ks.' + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem('ks.' + k, JSON.stringify(v)); } catch { /* private mode */ } },
};
const settings = {
  get delay() { return store.get('delay', 60); }, set delay(v) { store.set('delay', v); },
  get mode() { return store.get('mode', 'both'); }, set mode(v) { store.set('mode', v); },
  get size() { return store.get('size', 1); }, set size(v) { store.set('size', v); },
};

// ------------------------------------------------------------------ API

let loginWaiters = [];
async function api(path, opts = {}) {
  const res = await fetch(path, opts);
  if (res.status === 401 && KS.isApp) {
    if (!$('dlgServer').open) $('dlgServer').showModal();
    throw new Error('Wrong or missing API key — check it in ⚙ Settings.');
  }
  if (res.status === 401) {
    await new Promise((r) => { loginWaiters.push(r); if (!$('dlgLogin').open) $('dlgLogin').showModal(); });
    return api(path, opts);
  }
  const text = await res.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch { body = text; }
  if (!res.ok) throw new Error((body && body.detail) || `Server error ${res.status}`);
  return body;
}
const json = (method, body) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

$('loginBtn').onclick = async () => {
  const res = await fetch('/login', json('POST', { key: $('loginKey').value }));
  if (!res.ok) { $('loginErr').hidden = false; return; }
  $('loginErr').hidden = true;
  $('dlgLogin').close();
  loginWaiters.forEach((r) => r());
  loginWaiters = [];
};
$('loginKey').onkeydown = (e) => { if (e.key === 'Enter') $('loginBtn').click(); };
$('dlgLogin').oncancel = (e) => e.preventDefault();

let toastTimer;
function toast(msg, ms = 4000) {
  const t = $('toast');
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), ms);
}

document.querySelectorAll('dialog [data-close]').forEach((b) => (b.onclick = () => b.closest('dialog').close()));

// ------------------------------------------------------------------ library

let songs = [];
let pollTimer = null;
const LABEL = { lrclib: 'Synced lyrics', whisper: 'AI lyrics', manual: 'My lyrics' };

async function refreshLibrary() {
  try {
    songs = await api('/songs');
    $('libError').hidden = true;
  } catch (e) {
    $('libError').textContent = KS.isApp ? `Can't reach your PC (${store.get('server', 'not set')}). Start Karaoke Studio on the PC, check the Wi-Fi, or change the address in ⚙ Settings.` : e.message;
    $('libError').hidden = false;
  }
  renderLibrary();
  clearTimeout(pollTimer);
  if (songs.some((s) => !['done', 'error'].includes(s.status))) pollTimer = setTimeout(refreshLibrary, 3000);
}

function renderLibrary() {
  const q = $('search').value.trim().toLowerCase();
  const shown = songs.filter((s) => !q || `${s.title} ${s.artist || ''}`.toLowerCase().includes(q));
  $('empty').hidden = shown.length > 0;
  $('grid').innerHTML = shown.map((s) => {
    const ready = s.status === 'done', failed = s.status === 'error';
    const sub = [s.artist, LABEL[s.lyrics_source]].filter(Boolean).join(' · ');
    const thumb = s.status === 'queued' || s.status === 'downloading' ? '' : `style="background-image:url('${KS.U(`/songs/${s.id}/files/thumb.jpg`)}')"`;
    return `<div class="card ${ready ? 'ready' : ''}" data-id="${s.id}">
      <div class="thumb" ${thumb}>${thumb ? '' : '♪'}
        ${!ready && !failed ? `<div class="overlay"><span class="spinner"></span>${esc(s.progress)}</div>` : ''}
        ${failed ? `<div class="overlay fail">⚠ ${esc((s.error || 'Failed').slice(0, 400))}<button data-act="retry">↻ Retry</button></div>` : ''}
      </div>
      <div class="meta"><div><div class="t" title="${esc(s.title)}">${esc(s.title)}</div><div class="s">${esc(sub)}</div></div>
        ${ready || failed ? '<button class="icon-btn" data-act="delete" title="Delete">🗑</button>' : ''}</div>
    </div>`;
  }).join('');
}

$('grid').onclick = async (e) => {
  const card = e.target.closest('.card');
  if (!card) return;
  const id = card.dataset.id;
  const act = e.target.closest('[data-act]')?.dataset.act;
  if (act === 'delete') {
    const s = songs.find((x) => x.id === id);
    if (!confirm(`Delete "${s?.title}" and its recordings?`)) return;
    try { await api(`/songs/${id}`, { method: 'DELETE' }); } catch (err) { toast(err.message); }
    return refreshLibrary();
  }
  if (act === 'retry') {
    try { await api(`/songs/${id}/retry`, { method: 'POST' }); } catch (err) { toast(err.message); }
    return refreshLibrary();
  }
  if (card.classList.contains('ready')) location.hash = `#/play/${id}`;
};

$('search').oninput = renderLibrary;

$('btnAdd').onclick = async () => {
  const url = $('url').value.trim();
  if (!url) return $('url').focus();
  $('btnAdd').disabled = true;
  try {
    await api('/songs', json('POST', { url, language: $('lang').value }));
    $('url').value = '';
    toast('Added — making the karaoke now. This takes a few minutes.');
    refreshLibrary();
  } catch (e) { toast(e.message); } finally { $('btnAdd').disabled = false; }
};
$('url').onkeydown = (e) => { if (e.key === 'Enter') $('btnAdd').click(); };

$('fileInput').onchange = () => {
  const file = $('fileInput').files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append('file', file);
  fd.append('language', $('lang').value);
  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/songs/upload');
  xhr.upload.onprogress = (e) => e.lengthComputable && toast(`Uploading… ${Math.round((e.loaded * 100) / e.total)}%`, 60000);
  xhr.onload = () => {
    $('fileInput').value = '';
    if (xhr.status === 401) { $('dlgLogin').showModal(); return; }
    if (xhr.status >= 400) { toast(JSON.parse(xhr.responseText || '{}').detail || 'Upload failed'); return; }
    toast('Uploaded — making the karaoke now.');
    refreshLibrary();
  };
  xhr.onerror = () => toast('Upload failed');
  xhr.send(fd);
};

// ------------------------------------------------------------------ player state

const engine = new Engine($('video'));
let song = null;
let lyrics = { lines: [] };
let ref = new PitchTrack();
let offset = 0;
let session = null;
let busy = false;

engine.onLoading = (msg) => {
  $('stageMsg').hidden = !msg;
  if (msg) $('stageMsg').innerHTML = `<span class="spinner"></span><div>${esc(msg)}</div>`;
};
engine.onEnded = () => { if (session) finishSinging(); updatePlayButton(); };

async function openSong(id) {
  if (song?.id === id && engine.music) return; // already open (e.g. back from editor)
  closeSong();
  song = await api(`/songs/${id}`);
  if (song.status !== 'done') { toast('This song is still being made.'); location.hash = '#/'; return; }
  $('pTitle').textContent = song.title;
  $('pArtist').textContent = song.artist || '';
  offset = store.get('off.' + id, 0);
  const key = store.get('key.' + id, 0);
  $('guide').checked = false;
  updateKeyOffset(key);
  const [lyr, pitch] = await Promise.all([
    api(`/songs/${id}/lyrics`).catch(() => ({ lines: [] })),
    api(`/songs/${id}/pitch`).catch(() => null),
  ]);
  setLyrics(lyr);
  ref = pitch ? new PitchTrack(pitch.hop, pitch.midi) : new PitchTrack();
  $('pitch').hidden = ref.midi.length === 0;
  $('video').src = KS.U(`/songs/${id}/files/karaoke.mp4`);
  try {
    await engine.load(id, key);
  } catch (e) {
    engine.onLoading(null);
    $('stageMsg').hidden = false;
    $('stageMsg').innerHTML = `⚠ ${esc(e.message)}`;
    return;
  }
  $('seek').max = engine.duration;
  $('tDur').textContent = fmt(engine.duration);
  $('bigPlay').hidden = false;
  updatePlayButton();
}

function closeSong() {
  if (fixOpen) closeFix(); else flushFix();
  if (session) { session.cancel(); session = null; }
  engine.stop();
  song = null;
  lyricsIdx = -2;
  lyricsModeKey = '';
}

function setLyrics(l) {
  lyrics = l && l.lines ? l : { lines: [] };
  lyricsIdx = -2;
  lyricsModeKey = '';
  const src = $('pSource');
  src.hidden = !lyrics.lines.length;
  src.textContent = LABEL[lyrics.source] || lyrics.source || '';
  $('modeSeg').hidden = !lyrics.lines.some((x) => x.roman);
  markMode();
}

function updateKeyOffset(key = engine.key) {
  $('keyVal').textContent = key > 0 ? `+${key}` : `${key}`;
  $('offVal').textContent = `${offset >= 0 ? '+' : ''}${offset.toFixed(1)}s`;
  const locked = !!session;
  $('keyDown').disabled = locked || key <= -6;
  $('keyUp').disabled = locked || key >= 6;
  $('guide').disabled = locked;
}

function updatePlayButton() {
  $('btnPlay').textContent = engine.playing ? '❚❚' : '▶';
  $('bigPlay').hidden = engine.playing || !engine.music;
}

async function togglePlay() {
  if (!engine.music || busy) return;
  if (engine.playing && session) return finishSinging(); // pausing ends the take
  await engine.toggle();
  updatePlayButton();
}

async function changeKey(d) {
  if (session || busy || !engine.music) return;
  const k = clamp(engine.key + d, -6, 6);
  if (k === engine.key) return;
  busy = true;
  updateKeyOffset(k);
  try {
    await engine.setKey(k);
    store.set('key.' + song.id, k);
  } catch (e) {
    toast(e.message);
  } finally {
    busy = false;
    updateKeyOffset();
    updatePlayButton();
  }
}

function changeOffset(d) {
  offset = Math.round((offset + d) * 10) / 10;
  store.set('off.' + song.id, offset);
  updateKeyOffset();
  if (fixOpen) renderFixList();
}

function seekTo(t) {
  if (!engine.music) return;
  if (session) cancelSinging();
  engine.seek(t);
}

$('btnPlay').onclick = togglePlay;
$('bigPlay').onclick = togglePlay;
$('video').onclick = togglePlay;
$('btnBack5').onclick = () => seekTo(engine.time - 5);
$('btnFwd5').onclick = () => seekTo(engine.time + 5);
$('keyDown').onclick = () => changeKey(-1);
$('keyUp').onclick = () => changeKey(1);
$('offDown').onclick = () => changeOffset(-0.1);
$('offUp').onclick = () => changeOffset(0.1);
$('guide').onchange = async () => {
  busy = true;
  try { await engine.setGuide($('guide').checked); } catch (e) { toast(e.message); $('guide').checked = false; }
  busy = false;
  updatePlayButton();
};
let seeking = false;
$('seek').oninput = () => { seeking = true; $('tCur').textContent = fmt(+$('seek').value); };
$('seek').onchange = () => { seeking = false; seekTo(+$('seek').value); };

// ------------------------------------------------------------------ lyrics rendering

let lyricsIdx = -2;
let lyricsModeKey = '';
let wordEls = [];
let romanEl = null;
let curLine = null;

function lineAt(t) {
  const L = lyrics.lines;
  let lo = 0, hi = L.length - 1, ans = -1;
  while (lo <= hi) { const m = (lo + hi) >> 1; if (L[m].start <= t) { ans = m; lo = m + 1; } else hi = m - 1; }
  return ans;
}

function renderLyrics(t) {
  const box = $('lyrics');
  box.style.setProperty('--s', settings.size);
  const L = lyrics.lines;
  if (!L.length) {
    if (lyricsIdx !== -3) { box.innerHTML = '<div class="none">No lyrics yet — click “Edit lyrics” to add them.</div>'; lyricsIdx = -3; }
    return;
  }
  const idx = lineAt(t);
  const singing = idx >= 0 && t <= L[idx].end + 0.3;
  const big = singing ? idx : idx + 1;
  const mode = settings.mode;
  const key = `${big}|${mode}`;
  if (key !== lyricsModeKey) {
    lyricsModeKey = key;
    lyricsIdx = big;
    curLine = L[big] || null;
    const next = L[big + 1];
    const lineHtml = (line, cur) => {
      if (!line) return '';
      const hasRoman = !!line.roman;
      const showNative = mode !== 'roman' || !hasRoman;
      const showRoman = hasRoman && mode !== 'native';
      const words = line.words?.length ? line.words : [{ start: line.start, end: line.end, text: line.text }];
      const native = showNative
        ? (cur ? words.map((w, i) => `<span class="w" data-i="${i}">${esc(w.text)}</span>`).join(' ') : esc(line.text))
        : '';
      const roman = showRoman ? `<div class="roman ${cur ? 'w' : ''}">${esc(line.roman)}</div>` : '';
      return `<div class="line ${cur ? 'cur' : 'nxt'} ${showNative ? '' : 'solo-roman'}">${native}${roman}</div>`;
    };
    box.innerHTML = `<div class="dots"></div>${lineHtml(curLine, true)}${lineHtml(next, false)}`;
    wordEls = [...box.querySelectorAll('.cur > span.w')];
    romanEl = box.querySelector('.cur .roman.w');
  }
  if (!curLine) return;
  // word-by-word fill
  const words = curLine.words?.length ? curLine.words : [{ start: curLine.start, end: curLine.end }];
  wordEls.forEach((el, i) => {
    const w = words[i];
    const p = clamp((t - w.start) / Math.max(0.05, w.end - w.start), 0, 1);
    el.style.setProperty('--p', `${(p * 100).toFixed(1)}%`);
  });
  if (romanEl) {
    const s = words[0].start, e = words[words.length - 1].end;
    const p = e > s ? clamp((t - s) / (e - s), 0, 1) : t >= s ? 1 : 0;
    romanEl.style.setProperty('--p', `${(p * 100).toFixed(1)}%`);
  }
  // count-in dots before a line after a gap
  const cd = !singing ? curLine.start - t : 0;
  const dots = box.querySelector('.dots');
  const want = cd > 0.2 && cd < 4 ? [0, 1, 2].map((i) => `<i class="${cd <= 3 - i ? 'on' : ''}"></i>`).join('') : '';
  if (dots.innerHTML !== want) dots.innerHTML = want;
}

// ------------------------------------------------------------------ pitch strip

function drawPitch(t) {
  const c = $('pitch');
  if (c.hidden) return;
  const dpr = window.devicePixelRatio || 1;
  const W = c.clientWidth, H = c.clientHeight;
  if (c.width !== Math.round(W * dpr) || c.height !== Math.round(H * dpr)) { c.width = Math.round(W * dpr); c.height = Math.round(H * dpr); }
  const g = c.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  const css = getComputedStyle(document.documentElement);
  const ACC = css.getPropertyValue('--accent').trim() || '#34d3ff';
  const GOLD = css.getPropertyValue('--gold').trim() || '#ffc94d';
  g.clearRect(0, 0, W, H);
  const past = 2.5, future = 4, range = 13, k = engine.key;
  const center = ref.median + k;
  const x = (tt) => ((tt - (t - past)) / (past + future)) * W;
  const y = (m) => H / 2 - ((m - center) / range) * (H / 2 - 6);
  g.strokeStyle = '#ffffff12';
  g.lineWidth = 1;
  for (let s = -12; s <= 12; s += 6) { g.beginPath(); g.moveTo(0, y(center + s)); g.lineTo(W, y(center + s)); g.stroke(); }
  const i0 = Math.max(0, Math.floor((t - past) / ref.hop)), i1 = Math.min(ref.midi.length, Math.ceil((t + future) / ref.hop));
  const bw = (W * ref.hop) / (past + future) + 0.6;
  for (let i = i0; i < i1; i++) {
    const m = ref.midi[i];
    if (m == null) continue;
    const tt = i * ref.hop;
    g.fillStyle = tt <= t ? ACC : 'rgba(150,150,170,.45)';
    g.fillRect(x(tt), y(m + k) - 3, bw, 6);
  }
  if (session) {
    g.lineWidth = 3.5;
    g.lineCap = 'round';
    let prev = null;
    const pts = session.points;
    for (let i = Math.max(0, pts.length - 200); i < pts.length; i++) {
      const p = pts[i];
      if (p.midi == null || p.t < t - past) { prev = null; continue; }
      const r = ref.at(p.t);
      const target = r != null ? r + k : center;
      const d = pitchDiff(p.midi, target);
      const pt = [x(p.t), y(target + d)];
      if (prev && pt[0] - prev[0] < 8) {
        g.strokeStyle = r != null && Math.abs(d) <= 1 ? GOLD : ACC;
        g.beginPath(); g.moveTo(prev[0], prev[1]); g.lineTo(pt[0], pt[1]); g.stroke();
      }
      prev = pt;
    }
  }
  g.strokeStyle = '#ffffff60';
  g.lineWidth = 2;
  g.beginPath(); g.moveTo(x(t), 0); g.lineTo(x(t), H); g.stroke();
}

// ------------------------------------------------------------------ main loop

function frame() {
  try { tick(); } catch (e) { console.error(e); }
  requestAnimationFrame(frame);
}

function tick() {
  if (song && !$('viewPlayer').hidden) {
    const t = engine.time;
    engine.syncVideo();
    renderLyrics(t - offset);
    if (fixOpen) highlightFixRow(t - offset);
    drawPitch(t);
    if (!seeking) { $('seek').value = t; $('tCur').textContent = fmt(t); }
    if (session) $('liveScore').textContent = ref.midi.length ? Math.round(session.score) : '';
  } else if (song && !$('viewEditor').hidden && tapLines.length) {
    engine.syncVideo();
  }
}
requestAnimationFrame(frame);

// ------------------------------------------------------------------ singing

async function startSinging() {
  if (!engine.music || session || busy) return;
  if ($('guide').checked) { $('guide').checked = false; await engine.setGuide(false); }
  engine.ensureCtx();
  await engine.ctx.resume();
  const s = new SingSession({ engine, reference: ref, semitones: engine.key, delayMs: settings.delay });
  try {
    await s.start();
  } catch (e) {
    toast(e.name === 'NotAllowedError' ? 'Microphone blocked — allow it in the browser address bar.' : e.message, 7000);
    return;
  }
  session = s;
  $('recBadge').hidden = false;
  $('btnSing').textContent = '■ Stop & score';
  $('btnSing').classList.add('stop');
  updateKeyOffset();
  if (!engine.playing) await engine.play();
  updatePlayButton();
}

function resetSingUi() {
  $('recBadge').hidden = true;
  $('btnSing').textContent = '🎤 Sing & record';
  $('btnSing').classList.remove('stop');
  updateKeyOffset();
}

function cancelSinging() {
  session?.cancel();
  session = null;
  resetSingUi();
  toast('Recording stopped because you moved in the song.');
}

let take = null;
function finishSinging() {
  const s = session;
  if (!s) return;
  session = null;
  engine.pause();
  updatePlayButton();
  resetSingUi();
  const { wav, songStart } = s.stop();
  take = { wav, songStart, score: s.score, key: s.semitones };
  const melody = ref.midi.length > 0;
  $('resScoreBox').hidden = !melody;
  $('resGrade').textContent = grade(s.score);
  $('resScore').textContent = `${Math.round(s.score)} / 100`;
  $('resDetail').textContent = `Perfect notes: ${s.perfect}   Good: ${s.good}`;
  $('resNoVoice').hidden = !!wav;
  $('resAudio').hidden = true;
  $('resErr').hidden = true;
  $('resSave').hidden = !wav;
  $('resSave').disabled = false;
  $('resSave').textContent = '💾 Save recording';
  $('resDiscard').textContent = 'Discard';
  $('dlgResult').showModal();
}

$('btnSing').onclick = () => (session ? finishSinging() : startSinging());

$('resSave').onclick = async () => {
  if (!take?.wav) return;
  $('resSave').disabled = true;
  $('resSave').textContent = 'Mixing…';
  const fd = new FormData();
  fd.append('voice', take.wav, 'voice.wav');
  fd.append('song_start', take.songStart.toFixed(3));
  fd.append('semitones', take.key);
  fd.append('latency_ms', 0); // already corrected in the browser
  if (ref.midi.length) fd.append('score', take.score.toFixed(1));
  try {
    const r = await api(`/songs/${song.id}/recordings`, { method: 'POST', body: fd });
    const url = KS.U(`/songs/${song.id}/recordings/${r.id}.mp3`);
    take.recId = r.id;
    $('resPlayer').src = url;
    $('resDownload').href = url;
    $('resDownload').onclick = KS.isApp ? (e) => { e.preventDefault(); nativeSave(url, `${safeName(song.title)} - ${r.id}.mp3`); } : null;
    $('resDownload').download = `${song.title} - ${r.id}.mp3`;
    $('resAudio').hidden = false;
    $('resSave').hidden = true;
    $('resDiscard').textContent = 'Close';
    $('resPlayer').play().catch(() => {});
  } catch (e) {
    $('resErr').textContent = e.message;
    $('resErr').hidden = false;
    $('resSave').disabled = false;
    $('resSave').textContent = '💾 Save recording';
  }
};
$('resVideo').onclick = () => {
  if (!take?.recId) return;
  $('resPlayer').pause();
  $('dlgResult').close();
  openVideo(song.id, take.recId);
};
$('resDiscard').onclick = () => { $('resPlayer').pause(); $('dlgResult').close(); take = null; };

// ------------------------------------------------------------------ recordings list

$('btnRecs').onclick = async () => {
  $('recsList').innerHTML = '<span class="spinner"></span>';
  $('recsPlayer').hidden = true;
  $('dlgRecs').showModal();
  try {
    const recs = await api(`/songs/${song.id}/recordings`);
    $('recsList').innerHTML = recs.length ? recs.map((r) => {
      const d = new Date(r.created * 1000);
      const url = KS.U(`/songs/${song.id}/recordings/${r.id}.mp3`);
      return `<div class="rec-item" data-url="${url}">
        <div class="g">${r.score == null ? '♪' : grade(r.score)}</div>
        <div><div>${d.toLocaleString()}</div><div class="muted">${r.score == null ? 'No score' : 'Score ' + Math.round(r.score)}${r.semitones ? ` · key ${r.semitones > 0 ? '+' : ''}${r.semitones}` : ''}</div></div>
        <span class="acts"><button class="icon-btn" data-video="${r.id}" title="Make video &amp; share (WhatsApp, YouTube)">🎬</button>
        <a href="${url}" download="${esc(song.title)} - ${r.id}.mp3" title="Save MP3">⬇</a></span></div>`;
    }).join('') : '<p class="muted">No recordings yet. Press “Sing & record”.</p>';
  } catch (e) { $('recsList').textContent = e.message; }
};
$('recsList').onclick = (e) => {
  const dl = KS.isApp && e.target.closest('a[download]');
  if (dl) { e.preventDefault(); return nativeSave(dl.href, dl.getAttribute('download') || 'recording.mp3'); }
  const vid = e.target.closest('[data-video]');
  if (vid) { $('recsPlayer').pause(); $('dlgRecs').close(); return openVideo(song.id, vid.dataset.video); }
  const item = e.target.closest('.rec-item');
  if (!item || e.target.closest('a')) return;
  document.querySelectorAll('.rec-item').forEach((x) => x.classList.toggle('on', x === item));
  $('recsPlayer').hidden = false;
  $('recsPlayer').src = item.dataset.url;
  $('recsPlayer').play().catch(() => {});
};
$('dlgRecs').onclose = () => $('recsPlayer').pause();

// ------------------------------------------------------------------ lyrics mode + settings

function markMode() {
  document.querySelectorAll('#modeSeg button, #setMode button').forEach((b) => b.classList.toggle('on', b.dataset.mode === settings.mode));
}
document.querySelectorAll('#modeSeg button, #setMode button').forEach((b) => {
  b.onclick = () => { settings.mode = b.dataset.mode; markMode(); };
});

$('btnSettings').onclick = () => {
  $('latency').value = settings.delay;
  $('latVal').textContent = `${settings.delay} ms`;
  $('lyrSize').value = settings.size;
  $('sizeVal').textContent = `${Math.round(settings.size * 100)}%`;
  markMode();
  markTheme();
  if (KS.isApp) { $('setServer').value = store.get('server', ''); $('setKey').value = store.get('apiKey', ''); $('setTestMsg').textContent = ''; }
  else fetch('/network').then((r) => r.json()).then((n) => {
    $('phoneBox').hidden = !n.addresses?.length;
    $('phoneAddr').innerHTML = (n.addresses || []).map(esc).join('<br>');
  }).catch(() => {});
  fetch('/health').then((r) => r.json()).then((h) => { $('verLine').textContent = `Karaoke Studio version ${h.version || '1.0'}`; }).catch(() => {});
  $('dlgSettings').showModal();
};
function markTheme() {
  const t = document.documentElement.dataset.theme || 'orange';
  document.querySelectorAll('#setTheme button').forEach((b) => b.classList.toggle('on', b.dataset.theme === t));
}
document.querySelectorAll('#setTheme button').forEach((b) => {
  b.onclick = () => { document.documentElement.dataset.theme = b.dataset.theme; store.set('theme', b.dataset.theme); markTheme(); };
});

// ---- Android app: which PC to talk to
function normServer(v) {
  v = v.trim();
  if (!v) return '';
  if (!/^https?:\/\//i.test(v)) v = 'http://' + v;
  if (!/:\d+(\/|$)/.test(v.replace(/^https?:\/\//, ''))) v = v.replace(/\/+$/, '') + ':8000';
  return v.replace(/\/+$/, '');
}
async function testServer(addr, key) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 6000);
  try {
    const r = await (window.fetch)(addr + '/health', { signal: ctrl.signal });
    if (!r.ok) throw new Error('Not a Karaoke Studio server');
    const h = await r.json();
    if (h.auth) {
      const s = await (window.fetch)(addr + '/songs', { headers: key ? { 'X-API-Key': key } : {} });
      if (s.status === 401) throw new Error('Wrong API key');
    }
    return h;
  } catch (e) {
    throw new Error(e.name === 'AbortError' ? `No answer from ${addr}. Is Karaoke Studio running on the PC, and is the phone on the same Wi-Fi?` : e.message);
  } finally { clearTimeout(t); }
}
$('setTest').onclick = async () => {
  const addr = normServer($('setServer').value), key = $('setKey').value.trim();
  $('setTestMsg').textContent = 'Connecting…';
  try {
    const h = await testServer(addr, key);
    store.set('server', addr); store.set('apiKey', key);
    $('setTestMsg').textContent = `Connected ✓ (PC version ${h.version || '?'})`;
    refreshLibrary();
  } catch (e) { $('setTestMsg').textContent = e.message; }
};
$('srvGo').onclick = async () => {
  const addr = normServer($('srvAddr').value), key = $('srvKey').value.trim();
  if (!addr) return;
  $('srvGo').disabled = true; $('srvErr').hidden = true;
  try {
    await testServer(addr, key);
    store.set('server', addr); store.set('apiKey', key);
    $('dlgServer').close();
    route();
  } catch (e) { $('srvErr').textContent = e.message; $('srvErr').hidden = false; }
  finally { $('srvGo').disabled = false; }
};
$('dlgServer').oncancel = (e) => { if (!store.get('server', '')) e.preventDefault(); };
if (KS.isApp && !store.get('server', '')) setTimeout(() => $('dlgServer').showModal(), 0);

$('latency').oninput = () => { settings.delay = +$('latency').value; $('latVal').textContent = `${settings.delay} ms`; };
$('lyrSize').oninput = () => { settings.size = +$('lyrSize').value; $('sizeVal').textContent = `${Math.round(settings.size * 100)}%`; };

// ------------------------------------------------------------------ lyrics editor

let tapLines = [];
let stamps = [];
const STAMP = /^\s*(\[[^\]]*\]\s*)+/;

function edMsg(m) { $('edMsg').hidden = !m; $('edMsg').textContent = m || ''; }
function edBusy(b) {
  $('edSpin').hidden = !b;
  ['edTap', 'edAI', 'edOnline', 'edSave'].forEach((id) => ($(id).disabled = b));
}

async function openEditor() {
  if (session) cancelSinging();
  engine.pause();
  updatePlayButton();
  $('edTitle').textContent = song.title;
  $('edLang').value = lyrics.lines.length ? (lyrics.language || 'ta') : 'ta';
  if (!$('edLang').value) $('edLang').value = 'auto';
  edMsg('');
  showTap(false);
  $('edText').value = 'Loading…';
  const r = await fetch(`/songs/${song.id}/lyrics.lrc`);
  $('edText').value = r.ok ? await r.text() : '';
}

$('btnEdit').onclick = () => { if (!song) return; if (fixOpen) closeFix(); location.hash = `#/edit/${song.id}`; };
$('edBack').onclick = () => { location.hash = `#/play/${song.id}`; };

$('edSave').onclick = async () => {
  edBusy(true);
  try {
    const lang = $('edLang').value;
    const l = await api(`/songs/${song.id}/lyrics`, json('PUT', { lrc: $('edText').value, language: lang === 'auto' ? null : lang }));
    setLyrics(l);
    toast('Lyrics saved.');
    location.hash = `#/play/${song.id}`;
  } catch (e) { edMsg(e.message); } finally { edBusy(false); }
};

async function redo(source) {
  edBusy(true);
  edMsg(source === 'whisper' ? 'AI is listening to the vocals and writing lyrics… (1-5 minutes)' : 'Searching online for synced lyrics…');
  try {
    const before = (await api(`/songs/${song.id}`)).lyrics_version || 0;
    const lang = $('edLang').value;
    await api(`/songs/${song.id}/lyrics/redo`, json('POST', { source, language: lang === 'auto' ? null : lang }));
    const until = Date.now() + 15 * 60 * 1000;
    while (Date.now() < until && !$('viewEditor').hidden) {
      await new Promise((r) => setTimeout(r, 3000));
      const s = await api(`/songs/${song.id}`);
      if ((s.lyrics_version || 0) !== before) {
        const r = await fetch(`/songs/${song.id}/lyrics.lrc`);
        $('edText').value = await r.text();
        setLyrics(await api(`/songs/${song.id}/lyrics`));
        edMsg('Done — check the lines, then press Save (or go back to sing).');
        return;
      }
      if (/^(No synced|Lyrics failed)/.test(s.progress || '')) { edMsg(s.progress); return; }
    }
  } catch (e) { edMsg(e.message); } finally { edBusy(false); }
}
$('edAI').onclick = () => redo('whisper');
$('edOnline').onclick = () => redo('lrclib');

function showTap(on) {
  $('edMain').hidden = on;
  $('tapView').hidden = !on;
  $('edSave').hidden = on;
}

function renderTap() {
  const i = stamps.length;
  $('tapCount').textContent = `Line ${Math.min(i + 1, tapLines.length)} of ${tapLines.length}`;
  $('tapPrev').textContent = tapLines[i - 1] || '';
  $('tapNext').textContent = tapLines[i] || '';
  $('tapAfter').textContent = tapLines[i + 1] || '';
}

$('edTap').onclick = async () => {
  tapLines = $('edText').value.split('\n').map((l) => l.replace(STAMP, '').trim()).filter(Boolean);
  if (!tapLines.length) return edMsg('Paste the lyrics first — one line per row.');
  stamps = [];
  showTap(true);
  renderTap();
  engine.seek(0);
  await engine.play();
  $('tapBtn').focus();
};

function tap() {
  if (!tapLines.length || stamps.length >= tapLines.length) return;
  stamps.push(Math.max(0, engine.time - 0.15)); // reaction time
  if (stamps.length === tapLines.length) return finishTap();
  renderTap();
}
function undoTap() {
  if (!stamps.length) return;
  const t = stamps.pop();
  engine.seek(Math.max(0, t - 3));
  renderTap();
}
function finishTap() {
  engine.pause();
  $('edText').value = stamps.map((t, i) => {
    const m = Math.floor(t / 60), s = t - m * 60;
    return `[${String(m).padStart(2, '0')}:${s.toFixed(2).padStart(5, '0')}]${tapLines[i]}`;
  }).join('\n');
  tapLines = [];
  showTap(false);
  edMsg('Timed! Press Save to use these lyrics. Fine-tune later with “Lyrics timing” in the player.');
}
function cancelTap() { engine.pause(); tapLines = []; showTap(false); }
$('tapBtn').onclick = tap;
$('tapUndo').onclick = undoTap;
$('tapCancel').onclick = cancelTap;


// ------------------------------------------------------------------ fix lyrics while playing

let fixOpen = false;
let fixTimer = null;
let fixSaving = false;
let fixAgain = false;
let fixHighlighted = -1;
const REACTION = 0.15; // people press a little after they hear the line start
const lyrT = () => engine.time - offset; // position in lyrics time

function fmtT(s) { s = Math.max(0, s); const m = Math.floor(s / 60); return `${m}:${(s - m * 60).toFixed(1).padStart(4, '0')}`; }

function setFixStatus(text, cls = '') { const el = $('fixStatus'); el.textContent = text; el.className = `fix-status ${cls}`; }

function spreadWords(text, start, end) {
  const words = text.split(/\s+/).filter(Boolean);
  if (!words.length) return [];
  const span = Math.min(end - start, Math.max(1.5, 0.12 * text.length + 0.8));
  const total = words.reduce((a, w) => a + w.length + 1, 0);
  let t = start;
  return words.map((w) => { const d = (span * (w.length + 1)) / total; const o = { start: t, end: t + d, text: w }; t += d; return o; });
}

/** Re-sort lines, recompute where each ends, and refill word timings for edited lines. */
function relayout() {
  const L = lyrics.lines;
  L.sort((a, b) => a.start - b.start);
  L.forEach((l, i) => {
    const nxt = L[i + 1] ? L[i + 1].start : (engine.duration || l.start + 6);
    l.end = Math.max(l.start + 0.3, Math.min(nxt, l.start + 12));
    if (!l.words || !l.words.length || l.words.map((w) => w.text).join(' ') !== l.text.trim().split(/\s+/).join(' ')) {
      l.words = spreadWords(l.text, l.start, l.end);
      l._edited = true;
    }
  });
}

function setLineStart(line, t) {
  t = Math.max(0, t);
  const d = t - line.start;
  line.start = t;
  (line.words || []).forEach((w) => { w.start += d; w.end += d; });
}

function fixChanged({ rerender = true } = {}) {
  relayout();
  lyricsModeKey = '';
  if (rerender) renderFixList();
  setFixStatus('Saving soon…');
  clearTimeout(fixTimer);
  fixTimer = setTimeout(saveFix, 1200);
}

function renderFixList() {
  const L = lyrics.lines;
  $('fixList').innerHTML = L.length ? L.map((l, i) => `<div class="fix-row" data-i="${i}">
      <button class="tm" data-act="seek" title="Play from here">${fmtT(l.start + offset)}</button>
      <button class="sm" data-act="minus" title="0.1 s earlier">−</button>
      <button class="sm" data-act="plus" title="0.1 s later">+</button>
      <button class="sm" data-act="now" title="This line starts now">⏱</button>
      <input value="${esc(l.text)}" spellcheck="false" placeholder="type the words">
      <button class="sm del" data-act="del" title="Delete line">✕</button></div>`).join('')
    : '<p class="muted" style="padding:14px">No lyrics yet. Play the song, press “＋ Add line here” as each line starts, then type the words.</p>';
  fixHighlighted = -1;
}

function openFix() {
  if (!song || !engine.music) return;
  fixOpen = true;
  $('fixPanel').hidden = false;
  $('stage').classList.add('fixing');
  $('btnFix').classList.add('on');
  setFixStatus('');
  renderFixList();
}

function closeFix() {
  flushFix();
  fixOpen = false;
  $('fixPanel').hidden = true;
  $('stage').classList.remove('fixing');
  $('btnFix').classList.remove('on');
}

function flushFix() {
  if (fixTimer) { clearTimeout(fixTimer); fixTimer = null; saveFix(); }
}

async function saveFix() {
  fixTimer = null;
  if (!song) return;
  if (fixSaving) { fixAgain = true; return; }
  fixSaving = true;
  setFixStatus('Saving…');
  const songId = song.id;
  const payload = {
    language: lyrics.language || null,
    lines: lyrics.lines.map((l) => ({ start: l.start, text: l.text, words: l._edited ? null : l.words })),
  };
  try {
    const saved = await api(`/songs/${songId}/lyrics/lines`, json('PUT', payload));
    if (song?.id === songId) {
      // Don't swap lines under the cursor while someone is typing; the next save catches up.
      const typing = document.activeElement?.closest?.('#fixList');
      if (!typing && !fixAgain) {
        setLyrics(saved);
        if (fixOpen) renderFixList();
      }
      setFixStatus('Saved ✓', 'ok');
    }
  } catch (e) {
    setFixStatus(e.message, 'err');
  } finally {
    fixSaving = false;
    if (fixAgain) { fixAgain = false; saveFix(); }
  }
}

function stampNextLine() {
  const L = lyrics.lines;
  if (!L.length) return;
  const t = lyrT();
  const target = L[lineAt(t) + 1];
  if (!target) return;
  setLineStart(target, t - REACTION);
  fixChanged();
  toast(`“${target.text.slice(0, 40)}” now starts at ${fmtT(target.start + offset)}`, 2000);
}

$('btnFix').onclick = () => (fixOpen ? closeFix() : openFix());
$('fixClose').onclick = closeFix;

$('fixList').onclick = (e) => {
  const btn = e.target.closest('button[data-act]');
  if (!btn) return;
  const line = lyrics.lines[+btn.closest('.fix-row').dataset.i];
  if (!line) return;
  const act = btn.dataset.act;
  if (act === 'seek') return seekTo(Math.max(0, line.start + offset - 1));
  if (act === 'minus') setLineStart(line, line.start - 0.1);
  if (act === 'plus') setLineStart(line, line.start + 0.1);
  if (act === 'now') setLineStart(line, lyrT() - REACTION);
  if (act === 'del') lyrics.lines.splice(lyrics.lines.indexOf(line), 1);
  btn.blur();
  fixChanged();
};

$('fixList').oninput = (e) => {
  if (e.target.tagName !== 'INPUT') return;
  const line = lyrics.lines[+e.target.closest('.fix-row').dataset.i];
  if (!line) return;
  line.text = e.target.value;
  line.roman = null; // recalculated by the server on save
  fixChanged({ rerender: false });
};
$('fixList').onkeydown = (e) => { if (e.key === 'Enter' || e.key === 'Escape') e.target.blur(); };
$('fixList').addEventListener('focusout', () => { if (!fixTimer && !fixSaving) fixChanged({ rerender: false }); });

$('fixAdd').onclick = () => {
  const line = { start: Math.max(0, lyrT() - REACTION), end: 0, text: '', roman: null, words: [] };
  lyrics.lines.push(line);
  relayout();
  lyricsModeKey = '';
  renderFixList();
  const i = lyrics.lines.indexOf(line);
  const input = $('fixList').querySelector(`.fix-row[data-i="${i}"] input`);
  input?.scrollIntoView({ block: 'center' });
  input?.focus();
  setFixStatus('Type the words for the new line');
};

$('fixBake').onclick = () => {
  if (!offset) return toast('Lyrics timing is already 0.0s — nothing to apply.');
  lyrics.lines.forEach((l) => setLineStart(l, l.start + offset));
  offset = 0;
  store.set('off.' + song.id, 0);
  updateKeyOffset();
  fixChanged();
};

function highlightFixRow(t) {
  const idx = lineAt(t);
  if (idx === fixHighlighted) return;
  fixHighlighted = idx;
  const rows = $('fixList').querySelectorAll('.fix-row');
  rows.forEach((r, i) => r.classList.toggle('now', i === idx));
  if (idx >= 0 && rows[idx] && !document.activeElement?.closest?.('#fixList')) rows[idx].scrollIntoView({ block: 'center', behavior: 'smooth' });
}


// ------------------------------------------------------------------ share video (save / WhatsApp / YouTube)

let vid = null; // { songId, recId, timer }

function safeName(s) { return String(s || 'karaoke').replace(/[\\/:*?"<>|]+/g, ' ').trim().slice(0, 60) || 'karaoke'; }

function vidShow(part) {
  ['vidSetup', 'vidProgress', 'vidReady'].forEach((id) => ($(id).hidden = id !== part));
  $('vidMake').hidden = part !== 'vidSetup';
}

async function openVideo(songId, recId) {
  clearTimeout(vid?.timer);
  vid = { songId, recId, timer: null };
  $('vidErr').hidden = true;
  $('vidName').value = store.get('singer', '');
  $('vidRoman').checked = store.get('vidRoman', true);
  $('vidRoman').closest('label').hidden = !lyrics.lines.some((l) => l.roman);
  const isKaraoke = recId === 'karaoke';
  $('vidTitle').textContent = isKaraoke ? '📤 Save & share the karaoke' : '🎬 Share your performance';
  $('vidIntro').textContent = isKaraoke
    ? 'Makes an HD MP4 of the whole song: karaoke music (no singer) with the lyrics on screen, so others can sing along.'
    : 'Makes an MP4 of the song video with your voice and the lyrics on screen.';
  $('vidName').closest('label').firstChild.textContent = isKaraoke ? 'Your name, shown as “Made by …” (optional) ' : 'Your name, shown at the start (optional) ';
  vidShow('vidSetup');
  $('dlgVideo').showModal();
  try {
    const st = await api(`/songs/${songId}/recordings/${recId}/video`);
    if (st.status === 'done') vidReady();
    else if (st.status === 'rendering') { vidShow('vidProgress'); vidPoll(); }
  } catch { /* first time: stay on setup */ }
}

$('vidMake').onclick = async () => {
  if (!vid) return;
  const singer = $('vidName').value.trim();
  store.set('singer', singer);
  store.set('vidRoman', $('vidRoman').checked);
  $('vidErr').hidden = true;
  try {
    await api(`/songs/${vid.songId}/recordings/${vid.recId}/video`,
      json('POST', { singer, roman: $('vidRoman').checked, offset: store.get('off.' + vid.songId, 0) }));
    $('vidBar').style.width = '0%';
    $('vidPct').textContent = 'Making your video… 0%';
    vidShow('vidProgress');
    vidPoll();
  } catch (e) { $('vidErr').textContent = e.message; $('vidErr').hidden = false; }
};
$('vidRedo').onclick = () => { $('vidPreview').pause(); vidShow('vidSetup'); };

function vidPoll() {
  clearTimeout(vid.timer);
  vid.timer = setTimeout(async () => {
    if (!vid || !$('dlgVideo').open) return;
    try {
      const st = await api(`/songs/${vid.songId}/recordings/${vid.recId}/video`);
      if (st.status === 'done') return vidReady();
      if (st.status === 'error') {
        vidShow('vidSetup');
        $('vidErr').textContent = 'Could not make the video: ' + (st.error || 'unknown error').slice(-300);
        $('vidErr').hidden = false;
        return;
      }
      $('vidBar').style.width = `${st.progress || 0}%`;
      $('vidPct').textContent = `Making your video… ${st.progress || 0}%  (about as long as the song on a normal PC)`;
    } catch { /* keep trying */ }
    vidPoll();
  }, 1000);
}

function vidUrl() { return KS.U(`/songs/${vid.songId}/recordings/${vid.recId}.mp4`); }
function vidFileName() {
  const s = songs.find((x) => x.id === vid.songId) || song || {};
  return vid.recId === 'karaoke' ? `${safeName(s.title)} - karaoke.mp4` : `${safeName(s.title)} - my singing.mp4`;
}

function vidReady() {
  const url = `${vidUrl()}${vidUrl().includes('?') ? '&' : '?'}v=${Date.now()}`;
  $('vidPreview').src = url;
  $('vidSave').href = url;
  $('vidSave').onclick = KS.isApp ? (e) => { e.preventDefault(); nativeSave(vidUrl(), vidFileName()); } : null;
  $('vidSave').download = vidFileName();
  vidShow('vidReady');
}

function downloadFile(url, filename) {
  if (KS.isApp) return nativeSave(url, filename);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

// ---- Android app: save into Documents/KaraokeStudio, share through the Android share menu
async function nativeDownload(url, filename, directory) {
  const { Filesystem, FileTransfer } = KS.plugins;
  if (!Filesystem || !FileTransfer) throw new Error('File saving is not available in this app build.');
  const path = `KaraokeStudio/${filename.replace(/[\\/:*?"<>|]+/g, ' ')}`;
  try { await Filesystem.mkdir({ path: 'KaraokeStudio', directory, recursive: true }); } catch { /* exists */ }
  const { uri } = await Filesystem.getUri({ path, directory });
  await FileTransfer.downloadFile({ url, path: uri, progress: false });
  return uri;
}
async function nativeSave(url, filename) {
  toast('Saving…', 60000);
  try {
    await nativeDownload(url, filename, 'DOCUMENTS');
    toast(`Saved to Documents/KaraokeStudio/${filename}`, 6000);
  } catch (e) {
    try { await nativeShare(url, filename, filename); } catch { toast('Could not save: ' + e.message, 8000); }
  }
}
async function nativeShare(url, filename, text) {
  const { Share } = KS.plugins;
  toast('Preparing…', 60000);
  const uri = await nativeDownload(url, filename, 'CACHE');
  $('toast').hidden = true;
  if (!Share) throw new Error('Sharing is not available in this app build.');
  await Share.share({ title: filename, text, files: [uri], dialogTitle: 'Share with…' });
}

function shareText() {
  const s = song || songs.find((x) => x.id === vid?.songId) || {};
  const who = store.get('singer', '');
  if (vid?.recId === 'karaoke') return `🎤 Karaoke: ${s.title || 'song'} — sing along! (made with Karaoke Studio)`;
  return `🎤 ${who ? who + ' sings ' : ''}${s.title || 'a song'} — made with Karaoke Studio`;
}

$('vidWhats').onclick = async () => {
  const btn = $('vidWhats');
  btn.disabled = true;
  try {
    if (KS.isApp) {
      try { await nativeShare(vidUrl(), vidFileName(), shareText()); } catch (e) { if (!/cancel/i.test(e.message)) toast(e.message, 8000); }
      return;
    }
    // Windows / Android share sheet: lets you pick WhatsApp and attaches the video directly.
    if (navigator.canShare) {
      toast('Preparing the video for sharing…', 15000);
      const blob = await (await fetch(vidUrl())).blob();
      const file = new File([blob], vidFileName(), { type: 'video/mp4' });
      if (navigator.canShare({ files: [file] })) {
        $('toast').hidden = true;
        try { await navigator.share({ files: [file], title: vidFileName(), text: shareText() }); return; } catch (e) { if (e.name === 'AbortError') return; }
      }
    }
    // Fallback: save the file and open WhatsApp so it can be attached.
    downloadFile(vidUrl(), vidFileName());
    window.open(`https://wa.me/?text=${encodeURIComponent(shareText())}`, '_blank');
    toast('Video saved to your Downloads folder. In WhatsApp choose a chat, click 📎 and pick the video.', 10000);
  } finally {
    btn.disabled = false;
  }
};

$('vidYT').onclick = async () => {
  if (KS.isApp) {
    toast('Pick YouTube in the share menu to upload.', 4000);
    try { await nativeShare(vidUrl(), vidFileName(), shareText()); } catch (e) { if (!/cancel/i.test(e.message)) toast(e.message, 8000); }
    return;
  }
  downloadFile(vidUrl(), vidFileName());
  window.open('https://www.youtube.com/upload', '_blank');
  toast('Video saved to Downloads. On YouTube click “Select files”, choose it, add a title and publish.', 10000);
};

$('vidClose').onclick = () => $('dlgVideo').close();
$('btnShareKaraoke').onclick = () => {
  if (!song) return;
  if (session) cancelSinging();
  engine.pause();
  updatePlayButton();
  openVideo(song.id, 'karaoke');
};

// ------------------------------------------------------------------ fix lyrics with correct text from the web

function openPaste() {
  if (!song) return;
  $('pasteErr').hidden = true;
  $('pasteText').value = '';
  $('dlgPaste').showModal();
  $('pasteText').focus();
}
$('edPaste').onclick = openPaste;
$('fixPaste').onclick = () => { flushFix(); openPaste(); };
$('pasteGo').onclick = async () => {
  const text = $('pasteText').value.trim();
  if (!text) { $('pasteErr').textContent = 'Paste the lyrics first.'; $('pasteErr').hidden = false; return; }
  const songId = song.id;
  $('pasteErr').hidden = true;
  $('pasteGo').disabled = true;
  $('pasteGo').textContent = 'Starting…';
  try {
    const lang = /[\u0B80-\u0BFF]/.test(text) ? 'ta' : (lyrics.language || null);
    await api(`/songs/${songId}/lyrics/align`, json('POST', { text, language: lang }));
    // matching runs in the background: listen to the vocals and place every word
    const until = Date.now() + 20 * 60 * 1000;
    let st = null;
    while (Date.now() < until) {
      await new Promise((r) => setTimeout(r, 2000));
      st = await api(`/songs/${songId}`);
      if (st.align_status !== 'working') break;
      $('pasteGo').textContent = `${st.align_progress || 'Working'}…`;
    }
    if (!st || st.align_status !== 'done') throw new Error(st?.align_error || 'Matching took too long. Try again.');
    if (song?.id === songId) {
      setLyrics(await api(`/songs/${songId}/lyrics`));
      if (fixOpen) renderFixList();
      if (!$('viewEditor').hidden) {
        const lrc = await fetch(`/songs/${songId}/lyrics.lrc`);
        $('edText').value = await lrc.text();
      }
    }
    $('dlgPaste').close();
    const pct = Math.round((st.align_matched || 0) * 100);
    toast(st.align_method === 'voice'
      ? `Lyrics fixed ✓ and matched to the singer's voice (${pct}% of words). Play the song to check; nudge any line with 🛠 Fix lyrics.`
      : `Lyrics fixed ✓ using the old timing (${pct}% matched) — voice matching wasn't available. Check with 🛠 Fix lyrics.`, 10000);
  } catch (e) {
    $('pasteErr').textContent = e.message;
    $('pasteErr').hidden = false;
  } finally {
    $('pasteGo').disabled = false;
    $('pasteGo').textContent = '✨ Fix lyrics';
  }
};
$('dlgVideo').onclose = () => { clearTimeout(vid?.timer); $('vidPreview').pause(); };

// ------------------------------------------------------------------ keyboard

document.addEventListener('keydown', (e) => {
  if (e.target.closest('input, textarea, select, [contenteditable]') || document.querySelector('dialog[open]')) return;
  if (!$('tapView').hidden) {
    if (e.code === 'Space' || e.code === 'Enter') { e.preventDefault(); tap(); }
    else if (e.code === 'Backspace') { e.preventDefault(); undoTap(); }
    else if (e.code === 'Escape') cancelTap();
    return;
  }
  if ($('viewPlayer').hidden) return;
  if (fixOpen && e.code === 'KeyT') { e.preventDefault(); return stampNextLine(); }
  if (fixOpen && e.code === 'Escape') return closeFix();
  const map = {
    Space: togglePlay,
    ArrowRight: () => seekTo(engine.time + 5),
    ArrowLeft: () => seekTo(engine.time - 5),
    ArrowUp: () => changeKey(1),
    ArrowDown: () => changeKey(-1),
    Escape: () => (location.hash = '#/'),
  };
  if (map[e.code]) { e.preventDefault(); map[e.code](); }
});

// ------------------------------------------------------------------ routing

function show(view) {
  ['viewLibrary', 'viewPlayer', 'viewEditor'].forEach((v) => ($(v).hidden = v !== view));
  document.body.classList.toggle('in-player', view !== 'viewLibrary');
}

async function route() {
  const [, page, id] = location.hash.split('/');
  try {
    if (page === 'play' && id) {
      show('viewPlayer');
      await openSong(id);
    } else if (page === 'edit' && id) {
      if (song?.id !== id) { location.hash = `#/play/${id}`; return; }
      show('viewEditor');
      await openEditor();
    } else {
      if (song) closeSong();
      show('viewLibrary');
      refreshLibrary();
    }
  } catch (e) {
    toast(e.message);
  }
}
window.addEventListener('hashchange', route);
route();


// If the page and the server are different versions (server not restarted after an update), say so.
if (!KS.isApp || store.get('server', '')) fetch('/health').then((r) => r.json()).then((h) => {
  if (h.version === APP_VERSION) return;
  const b = document.createElement('div');
  b.className = 'banner error';
  b.style.cssText = 'position:fixed;left:50%;top:12px;transform:translateX(-50%);z-index:60;max-width:92vw';
  b.textContent = KS.isApp
    ? `⚠ Karaoke Studio on your PC is version ${h.version || 'older'}, this app is ${APP_VERSION}. Install the latest update on the PC (KaraokeStudio-Update.exe).`
    : '⚠ Karaoke Studio was updated but is still running the old version. Close the black “Karaoke Studio” window (or restart the PC), then open Karaoke Studio again.';
  document.body.appendChild(b);
}).catch(() => {});
