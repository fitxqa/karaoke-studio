// Runs before app.js. In the browser on the PC everything is same-origin and nothing changes.
// Inside the Android app (Capacitor) the page is local, so server calls go to the PC's address
// saved in Settings, with the API key added.
(function () {
  const isApp = !!(window.Capacitor && window.Capacitor.isNativePlatform && window.Capacitor.isNativePlatform());
  const get = (k, d) => { try { const v = localStorage.getItem('ks.' + k); return v === null ? d : JSON.parse(v); } catch { return d; } };

  const KS = {
    isApp,
    get base() { return isApp ? String(get('server', '')).replace(/\/+$/, '') : ''; },
    get key() { return isApp ? String(get('apiKey', '')) : ''; },
    /** Full URL for media/links (video src, images, downloads). */
    U(path) {
      if (!isApp || !path || /^(https?:|blob:|data:)/.test(path)) return path;
      const k = KS.key;
      return KS.base + path + (k ? (path.includes('?') ? '&' : '?') + 'key=' + encodeURIComponent(k) : '');
    },
    plugins: (window.Capacitor && window.Capacitor.Plugins) || {},
  };
  window.KS = KS;

  // Theme: orange (black + orange + white) is the default; light = white + orange; blue = classic.
  document.documentElement.dataset.theme = get('theme', 'orange');

  if (!isApp) return;
  document.documentElement.classList.add('in-app');

  const nativeFetch = window.fetch.bind(window);
  window.fetch = (input, init = {}) => {
    if (typeof input === 'string' && input.startsWith('/') && !input.startsWith('//')) {
      input = KS.base + input;
      const headers = new Headers(init.headers || {});
      if (KS.key) headers.set('X-API-Key', KS.key);
      init = { ...init, headers };
    }
    return nativeFetch(input, init);
  };

  const open = XMLHttpRequest.prototype.open;
  const send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this._ksLocal = typeof url === 'string' && url.startsWith('/');
    return open.call(this, method, this._ksLocal ? KS.base + url : url, ...rest);
  };
  XMLHttpRequest.prototype.send = function (body) {
    if (this._ksLocal && KS.key) this.setRequestHeader('X-API-Key', KS.key);
    return send.call(this, body);
  };
})();
