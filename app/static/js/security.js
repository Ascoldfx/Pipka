/* Install CSRF protection before the dashboard's application code runs. */
(() => {
  if (window.__pipkaCsrfFetchInstalled) return;
  window.__pipkaCsrfFetchInstalled = true;

  const unsafeMethods = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
  const originalFetch = window.fetch.bind(window);

  function readCsrfCookie() {
    const match = document.cookie.match(/(?:^|; )csrf_token=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  window.fetch = (input, init = {}) => {
    const requestMethod = typeof input === 'string' ? 'GET' : (input.method || 'GET');
    const method = (init.method || requestMethod).toUpperCase();
    if (unsafeMethods.has(method)) {
      const token = readCsrfCookie();
      if (token) {
        init.headers = new Headers(init.headers || {});
        if (!init.headers.has('X-CSRF-Token')) {
          init.headers.set('X-CSRF-Token', token);
        }
      }
    }
    return originalFetch(input, init);
  };
})();
