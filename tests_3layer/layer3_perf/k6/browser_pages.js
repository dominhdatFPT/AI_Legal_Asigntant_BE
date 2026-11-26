import { browser } from 'k6/browser';
import { b64decode, b64encode } from 'k6/encoding';
import { Trend } from 'k6/metrics';

const budgets = JSON.parse(open('../budgets.json'));
const { origin, pages, network, interaction, image_debt: imageDebt } = budgets.browser;
const gateway = budgets.load.origin;
const profile = __ENV.PROFILE || 'desktop';
const mobile = profile === 'mobile';
const timing = budgets.browser[profile];
const runs = Number(__ENV.RUNS || timing.runs);
const journey = __ENV.PERF_JOURNEY === '1';
const tokens = __ENV.PERF_TOKENS ? JSON.parse(open(__ENV.PERF_TOKENS)) : null;
if (!['desktop', 'mobile'].includes(profile) || ![1, 3].includes(runs) || (journey && mobile)) {
  throw new Error('Use PROFILE=desktop|mobile and RUNS=1|3. The chat journey uses desktop.');
}
const selectedPages = Object.entries(pages);
// After image_debt.expires the drum PNG counts against the normal image budget.
const debtOpen = Date.now() <= Date.parse(`${imageDebt.expires}T23:59:59Z`);
if (Number.isNaN(Date.parse(imageDebt.expires))) throw new Error('image_debt.expires must be YYYY-MM-DD');

const names = [
  'errors', 'unstubbed', 'requests', 'api_calls', 'duplicates', 'sessions', 'eventsource', 'websocket',
  'js', 'css', 'images', 'drum', 'gzip_failures', 'blocking', 'cache', 'warm_bytes',
  'lcp', 'fcp', 'ttfb', 'auth_ready', 'cls', 'tbt', 'inp', 'frame_p95', 'dropped_fraction',
  'chat_open', 'chat_first_text',
];
const metrics = Object.fromEntries(names.map((name) => [name, new Trend(`perf_browser_${name}`)]));
const thresholds = {};
const failures = [];

function limit(metric, tags, expression) {
  const key = `perf_browser_${metric}{${Object.entries(tags).map(([k, v]) => `${k}:${v}`).join(',')}}`;
  thresholds[key] = [expression];
}

if (journey) {
  const tags = { page: 'user-dashboard', phase: 'journey' };
  limit('errors', tags, 'max<=0');
  limit('unstubbed', tags, 'max<=0');
  limit('chat_open', tags, `max<=${interaction.chat_open}`);
  limit('chat_first_text', tags, `max<=${interaction.chat_first_text}`);
} else {
  for (const [name, page] of selectedPages) {
    for (const phase of ['cold', 'warm']) {
      const tags = { page: name, phase };
      limit('errors', tags, `max<=${network.errors}`);
      limit('unstubbed', tags, `max<=${network.unstubbed}`);
      if (!mobile) {
        const upper = {
          requests: page.requests, api_calls: page.gateway_calls, duplicates: network.api_duplicates,
          sessions: network.sessions, eventsource: network.eventsource, websocket: network.websocket,
          gzip_failures: network.gzip_failures, blocking: network.blocking,
        };
        if (phase === 'cold') {
          Object.assign(upper, {
            js: page.role === 'admin' ? network.admin_js_bytes : network.js_bytes,
            css: network.css_bytes, images: network.image_bytes, drum: imageDebt.bytes,
          });
        }
        // k6/browser disables Chrome HTTP cache page-wide while page.route() is active (verified cache=0);
        // keep recording warm cache/warm_bytes telemetry, while gating static caching via HTTP Cache-Control/304 in P2.4.
        for (const [metric, value] of Object.entries(upper)) limit(metric, tags, `max<=${value}`);
      }
      if (phase === 'cold') {
        for (const metric of ['ttfb', 'fcp', 'lcp', 'auth_ready', 'cls', 'tbt']) {
          if (timing[metric] !== undefined && (metric !== 'auth_ready' || page.role)) {
            limit(metric, tags, `${mobile ? 'p(50)' : 'max'}<=${timing[metric]}`);
          }
        }
      }
    }
    if (mobile) {
      limit('errors', { page: name, phase: 'interaction' }, `max<=${network.errors}`);
      limit('unstubbed', { page: name, phase: 'interaction' }, `max<=${network.unstubbed}`);
      limit('inp', { page: name, phase: 'interaction' }, `p(50)<=${interaction.inp}`);
      if (name === 'welcome') {
        limit('frame_p95', { page: name, phase: 'interaction' }, `p(50)<=${interaction.frame_p95}`);
        limit('dropped_fraction', { page: name, phase: 'interaction' }, `p(50)<=${interaction.dropped_fraction}`);
      }
    }
  }
}

export const options = {
  scenarios: {
    ui: {
      executor: 'shared-iterations', vus: 1, iterations: 1,
      maxDuration: mobile ? timing.max_duration : journey ? '75s' : '150s',
      options: { browser: { type: 'chromium' } },
    },
  },
  summaryTrendStats: ['min', 'med', 'p(50)', 'p(95)', 'p(99)', 'max', 'count'],
  thresholds,
};

const firebase = {
  'firebase-app.js': 'export const initializeApp = () => ({});',
  'firebase-auth.js': [
    'export const getAuth = () => ({});',
    'export class GoogleAuthProvider { setCustomParameters() {} }',
    'export const signOut = async () => {};',
    ...[
      'createUserWithEmailAndPassword', 'deleteUser', 'sendEmailVerification', 'updateProfile',
      'signInWithEmailAndPassword', 'signInWithPopup', 'confirmPasswordReset', 'sendPasswordResetEmail',
      'verifyPasswordResetCode',
    ].map((name) => `export const ${name} = async () => { throw new Error("Firebase operation outside perf scope"); };`),
  ].join('\n'),
  'firebase-firestore.js': [
    'export const getFirestore = () => ({});',
    'export const doc = () => ({});',
    'export const serverTimestamp = () => null;',
    'export const setDoc = async () => {};',
    'export const deleteDoc = async () => {};',
  ].join('\n'),
};
const pixel = b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgYAAAAAMAAWgmWQ0AAAAASUVORK5CYII=');
const stubHeaders = { 'Access-Control-Allow-Origin': '*', 'Cache-Control': 'public, max-age=3600' };

function local(url, base) {
  return url === base || url.startsWith(`${base}/`) || url.startsWith(`${base}?`);
}

async function stubRoute(route, journal) {
  const url = route.request().url();
  if (local(url, origin) || local(url, gateway)) return route.continue();
  const match = /^https:\/\/www\.gstatic\.com\/firebasejs\/12\.19\.0\/([^/?]+)$/.exec(url);
  if (match && Object.hasOwn(firebase, match[1])) {
    return route.fulfill({ body: firebase[match[1]], contentType: 'text/javascript', headers: stubHeaders });
  }
  if (/^https:\/\/fonts\.googleapis\.com\/css2?[?]/.test(url)
      || url === 'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css') {
    return route.fulfill({ body: '', contentType: 'text/css', headers: stubHeaders });
  }
  if (/^https:\/\/(images\.unsplash\.com\/|lh3\.googleusercontent\.com\/)/.test(url)) {
    return route.fulfill({ body: pixel, contentType: 'image/png', headers: stubHeaders });
  }
  if (url === 'https://cdn.jsdelivr.net/npm/@lottiefiles/dotlottie-wc@0.9.28/dist/dotlottie-wc.js') {
    return route.fulfill({
      body: "customElements.define('dotlottie-wc', class extends HTMLElement {});",
      contentType: 'text/javascript', headers: stubHeaders,
    });
  }
  journal.unstubbed.push(url);
  return route.abort();
}

function initPage() {
  const state = {
    fetches: [], sockets: [], streams: [], errors: [], lcp: 0, cls: 0, tasks: [],
    authReady: null, role: null, events: [], first: [], interactionPaint: null, lastActivity: performance.now(),
  };
  window.__perf = state;
  performance.setResourceTimingBufferSize(1000);
  const now = () => performance.now();
  const absolute = (value) => new URL(value, location.href).href;
  const note = () => { state.lastActivity = now(); };
  const fetch = window.fetch;
  window.fetch = async function (input, init) {
    const row = {
      method: (init?.method || input.method || 'GET').toUpperCase(),
      url: absolute(input.url || input),
      start: now(), ms: null, status: null,
    };
    state.fetches.push(row);
    note();
    try {
      const response = await fetch.call(this, input, init);
      row.status = response.status;
      return response;
    } catch (error) {
      row.status = 0;
      throw error;
    } finally {
      row.ms = now() - row.start;
      note();
    }
  };
  for (const [name, log] of [['EventSource', state.streams], ['WebSocket', state.sockets]]) {
    window[name] = new Proxy(window[name], {
      construct(target, args, type) {
        const connection = Reflect.construct(target, args, type);
        log.push(absolute(args[0]));
        connection.addEventListener('error', () => state.errors.push(`${name}: ${args[0]}`), { once: true });
        return connection;
      },
    });
  }
  addEventListener('error', (event) => {
    if (event.message) state.errors.push(`pageerror: ${event.message}`);
  });
  addEventListener('unhandledrejection', (event) => state.errors.push(`rejection: ${event.reason}`));
  let lastShift = 0;
  let shiftStart = 0;
  let sessionShift = 0;
  const watch = (type, visit, options = {}) => {
    const observer = new PerformanceObserver((list) => list.getEntries().forEach(visit));
    observer.observe({ type, buffered: true, ...options });
  };
  watch('largest-contentful-paint', (entry) => { state.lcp = entry.startTime; });
  watch('layout-shift', (entry) => {
    if (entry.hadRecentInput) return;
    if (entry.startTime - lastShift >= 1000 || entry.startTime - shiftStart >= 5000) {
      sessionShift = 0;
      shiftStart = entry.startTime;
    }
    sessionShift += entry.value;
    lastShift = entry.startTime;
    state.cls = Math.max(state.cls, sessionShift);
  });
  watch('longtask', (entry) => state.tasks.push({ start: entry.startTime, duration: entry.duration }));
  watch('event', (entry) => {
    if (entry.interactionId) state.events.push({ start: entry.startTime, duration: entry.duration });
  }, { durationThreshold: 16 });
  watch('first-input', (entry) => state.first.push({ start: entry.startTime, duration: entry.duration }));
  addEventListener('click', (event) => {
    if (!event.isTrusted) return;
    requestAnimationFrame(() => requestAnimationFrame(() => {
      state.interactionPaint = now() - event.timeStamp;
    }));
  }, { capture: true, once: true });
  new MutationObserver((changes) => {
    if (state.authReady !== null || !changes.some((change) =>
      Array.from(change.removedNodes).some((node) => node.id === 'auth-pending'))) return;
    state.authReady = now();
    note();
  }).observe(document, { childList: true, subtree: true });
  addEventListener('ai-legal:user-ready', (event) => {
    state.role = event.detail?.role;
    if (!document.getElementById('auth-pending')) state.authReady = now();
    note();
  });
}

async function withPage(spec, action) {
  const context = await browser.newContext(mobile ? {
    viewport: { width: timing.width, height: timing.height },
    deviceScaleFactor: 2, isMobile: true, hasTouch: true,
  } : { viewport: { width: 1440, height: 900 } });
  let page = null;
  try {
    if (spec.role) {
      if (!tokens) throw new Error('PERF_TOKENS is required for protected pages');
      const token = spec.role === 'admin' ? tokens.admin : tokens.citizens[0];
      await context.addCookies([{ name: 'access_token', value: token, url: gateway, httpOnly: true, sameSite: 'Lax' }]);
    }
    await context.addInitScript(`(${initPage.toString()})()`);
    page = await context.newPage();
    const journal = { errors: [], unstubbed: [], statuses: {}, requests: [] };
    page.on('console', (message) => {
      if (message.type() === 'error') journal.errors.push(`console.error: ${message.text()}`);
    });
    page.on('request', (request) => {
      const url = request.url();
      journal.requests.push({ url, method: request.method() });
      // Plain-HTTP hosts bypass the HTTPS-only route below; K6_BROWSER_ARGS proxy-server=http://127.0.0.1:1
      // (no DIRECT fallback, probes.py) fails them, while proxy-bypass-list lets only 127.0.0.1/localhost through.
      if (/^http:\/\//.test(url) && !local(url, origin) && !local(url, gateway)) journal.unstubbed.push(url);
    });
    page.on('response', (response) => {
      const url = response.url();
      journal.statuses[url] = response.status();
      if (response.status() >= 400) journal.errors.push(`${response.status()} ${url}`);
    });
    page.on('requestfailed', (request) => {
      const error = typeof request.failure === 'function' ? request.failure()?.errorText : undefined;
      // Reload closes the old document's live stream by design. A failing live stream
      // is still caught by the EventSource error hook in initPage.
      if ((error === undefined || error === 'net::ERR_ABORTED')
          && request.url() === `${gateway}/api/notifications/stream`) return;
      journal.errors.push(`${error ?? 'request failed'} ${request.url()}`);
    });
    // Every stubbed CDN is HTTPS; FE (127.0.0.1:3000) and gateway (127.0.0.1:18081) are plain HTTP,
    // so they never reach a user route handler.
    // shortcut: Playwright/Puppeteer disable the HTTP cache page-wide while any route is active. If k6 does
    // too, warm cache_misses lists every FE asset; then move P2.4 to Cache-Control/304 checks over httpx.
    await page.route(/^https:\/\//, (route) => stubRoute(route, journal));
    if (mobile) {
      await page.throttleNetwork({
        latency: timing.rtt_ms, download: timing.down_bps / 8, upload: timing.up_bps / 8,
      });
      await page.throttleCPU({ rate: timing.cpu });
    }
    await action(page, journal);
  } finally {
    // k6/browser leaves route handlers hanging IterEnd unless page.close() runs before context.close().
    try {
      if (page) await page.close();
    } finally {
      await context.close();
    }
  }
}

async function settle(page, spec) {
  await page.waitForFunction((protectedPage) => {
    const state = window.__perf;
    return state && (!protectedPage || state.authReady !== null) &&
      state.fetches.every((call) => call.ms !== null) && performance.now() - state.lastActivity >= 400;
  }, { timeout: 15000 }, Boolean(spec.role));
  if (page.url() !== origin + spec.path) throw new Error(`Unexpected navigation: ${page.url()}`);
}

function clearJournal(journal) {
  journal.errors.length = 0;
  journal.unstubbed.length = 0;
  journal.requests.length = 0;
  journal.statuses = {};
}

async function readLoad(page, journal, spec, coldBytes) {
  const data = await page.evaluate(() => {
    const values = window.__perf;
    const nav = performance.getEntriesByType('navigation')[0];
    const fcp = performance.getEntriesByName('first-contentful-paint')[0]?.startTime || 0;
    return {
      ...values, ttfb: nav.responseStart, fcp,
      tbt: values.tasks.filter((task) => task.start >= fcp)
        .reduce((sum, task) => sum + Math.max(0, task.duration - 50), 0),
      resources: [nav, ...performance.getEntriesByType('resource')].map((resource) => ({
        url: resource.name, initiator: resource.initiatorType, encoded: resource.encodedBodySize,
        decoded: resource.decodedBodySize, transfer: resource.transferSize, status: resource.responseStatus,
        blocking: resource.renderBlockingStatus === 'blocking',
      })),
    };
  });
  if (!(data.fcp > 0 && data.lcp > 0 && data.ttfb > 0) || (spec.role && data.role !== spec.role)) {
    throw new Error(`Missing paint or authenticated role for ${spec.path}`);
  }
  const seen = new Set();
  const api = data.fetches.filter((call) => {
    const path = call.url.replace(/^https?:\/\/[^/]+/, '');
    return path.startsWith('/api/');
  });
  const duplicates = [];
  for (const call of api.filter((row) => row.method === 'GET')) {
    if (seen.has(call.url)) duplicates.push(call.url);
    seen.add(call.url);
  }
  const staticBytes = { js: 0, css: 0, images: 0, drum: 0 };
  const errors = [...journal.errors, ...data.errors];
  for (const call of data.fetches.filter((item) => item.status === 0 || item.status >= 400)) {
    errors.push(`fetch ${call.status}: ${call.method} ${call.url}`);
  }
  const gzipFailures = [];
  const cacheMisses = [];
  let staticCount = 0;
  let cached = 0;
  let bytes = 0;
  for (const resource of data.resources.filter((row) => local(row.url, origin))) {
    const clean = resource.url.split('?')[0];
    const filename = clean.slice(origin.length);
    const kind = /\.(?:js|mjs)$/i.test(clean) ? 'js' : /\.css$/i.test(clean) ? 'css' :
      /\.(?:png|jpe?g|gif|webp|avif|svg|ico)$/i.test(clean) || resource.initiator === 'img' ? 'images' : null;
    if (kind) {
      const hasAllowance = debtOpen && imageDebt.pages.some((name) => pages[name].path === spec.path);
      const bucket = filename === imageDebt.path && hasAllowance ? 'drum' : kind;
      staticBytes[bucket] += resource.encoded;
      staticCount++;
      const revalidated = resource.status === 304 || journal.statuses[resource.url] === 304;
      if (resource.transfer === 0 || revalidated) cached++;
      if (!revalidated && resource.transfer > 0) {
        bytes += resource.encoded;
        cacheMisses.push(resource.url);
      }
    }
    const isText = ['js', 'css'].includes(kind) || /\.svg$/i.test(clean) ||
      resource.initiator === 'navigation' || filename.startsWith('/api/');
    if (isText && resource.decoded >= network.gzip_min_bytes && resource.encoded >= resource.decoded) {
      gzipFailures.push(resource.url);
    }
  }
  const values = {
    errors: errors.length, unstubbed: journal.unstubbed.length,
    requests: journal.requests.filter((item) => local(item.url, origin)).length,
    api_calls: api.filter((call) => local(call.url, gateway)).length,
    duplicates: duplicates.length, sessions: api.filter((call) => call.url.includes('/api/users/auth/session')).length,
    eventsource: data.streams.length, websocket: data.sockets.length, ...staticBytes,
    gzip_failures: gzipFailures.length, blocking: data.resources.filter((resource) => resource.blocking).length,
    cache: staticCount ? cached / staticCount : 0, warm_bytes: coldBytes ? bytes / coldBytes : 0,
    lcp: data.lcp, fcp: data.fcp, ttfb: data.ttfb, auth_ready: data.authReady ?? 0, cls: data.cls, tbt: data.tbt,
  };
  return {
    values, bytes, image_total_bytes: staticBytes.images + staticBytes.drum,
    waterfall: data.resources, fetches: api, sockets: data.sockets, streams: data.streams,
    errors, duplicates, gzipFailures, cache_misses: cacheMisses, unstubbed: [...journal.unstubbed],
  };
}

function record(name, phase, run, sample) {
  for (const [metric, value] of Object.entries(sample.values)) metrics[metric].add(value, { page: name, phase });
  publish('pages', { page: name, phase, run, ...sample });
}

function publish(kind, data) {
  console.log(`PERF_BROWSER_PAYLOAD ${b64encode(JSON.stringify({ kind, data }))}`);
}

function recordFailure(message) {
  failures.push(message);
  publish('failures', message);
}

async function measureInteraction(page, journal, name, spec, run) {
  const values = {};
  if (name === 'welcome') {
    const gaps = await page.evaluate((duration) => new Promise((resolve) => {
      const frames = [];
      let start;
      let previous;
      function frame(now) {
        if (start === undefined) start = now;
        if (previous !== undefined) frames.push(now - previous);
        previous = now;
        const fraction = Math.min(1, (now - start) / duration);
        window.scrollTo(0, fraction * (document.documentElement.scrollHeight - innerHeight));
        if (fraction < 1) requestAnimationFrame(frame);
        else resolve(frames);
      }
      requestAnimationFrame(frame);
    }), interaction.scroll_ms);
    if (!gaps.length) throw new Error('No animation frames during scripted scroll');
    const sorted = gaps.slice().sort((a, b) => a - b);
    values.frame_p95 = sorted[Math.ceil(0.95 * sorted.length) - 1];
    const dropped = gaps.reduce((sum, gap) => sum + Math.max(0, Math.round(gap / (1000 / 60)) - 1), 0);
    values.dropped_fraction = dropped / (gaps.length + dropped);
  }
  const from = await page.evaluate(() => performance.now());
  await page.locator(spec.interaction).click();
  await page.waitForFunction(() => window.__perf.interactionPaint !== null);
  const observed = await page.evaluate((start) => {
    const { events, first, interactionPaint } = window.__perf;
    const matching = [...events, ...first].filter((entry) => entry.start >= start);
    return { ms: matching.length ? Math.max(...matching.map((entry) => entry.duration)) : interactionPaint,
      source: matching.length ? 'event-timing' : 'paint-estimate' };
  }, from);
  values.inp = observed.ms;
  const errors = [...journal.errors, ...await page.evaluate(() => window.__perf.errors)];
  values.errors = errors.length;
  values.unstubbed = journal.unstubbed.length;
  record(name, 'interaction', run, { values, inp_source: observed.source, errors, unstubbed: [...journal.unstubbed] });
}

async function measurePage(name, spec, run) {
  await withPage(spec, async (page, journal) => {
    await page.goto(origin + spec.path, { waitUntil: 'load' });
    await settle(page, spec);
    const cold = await readLoad(page, journal, spec);
    record(name, 'cold', run, cold);
    clearJournal(journal);
    // M1: page.route disables the HTTP cache, so "warm" is a reload with a cold HTTP cache (only the
    // document/JS state differs). Static caching is gated by P2.4 instead.
    await page.reload({ waitUntil: 'load' });
    await settle(page, spec);
    record(name, 'warm', run, await readLoad(page, journal, spec, cold.bytes));
    if (mobile) await measureInteraction(page, journal, name, spec, run);
  });
}

async function chatJourney() {
  const name = 'user-dashboard';
  await withPage(pages[name], async (page, journal) => {
    await page.goto(origin + pages[name].path, { waitUntil: 'load' });
    await settle(page, pages[name]);
    const openedAt = await page.evaluate(() => performance.now());
    await page.locator('#ai-chat-facade-toggle').click();
    await page.locator('#admin-ai-panel .admin-ai-input').waitFor({ state: 'visible' });
    const opened = await page.evaluate((start) => performance.now() - start, openedAt);
    await page.locator('#admin-ai-panel .admin-ai-input').fill('Điều 9 Luật Đất đai phân loại đất như thế nào?');
    await page.evaluate(() => {
      const selector = '.admin-ai-turn-assistant .admin-ai-bubble:not(.admin-ai-status)';
      const old = new Set(document.querySelectorAll(selector));
      document.addEventListener('submit', () => {
        window.__perf.chatFirst = null;
        const start = performance.now();
        new MutationObserver((_, observer) => {
          const newBubble = Array.from(document.querySelectorAll(selector)).find((bubble) =>
            !old.has(bubble) && bubble.getClientRects().length && bubble.innerText.includes('CHẾ ĐỘ TEST'));
          if (newBubble) {
            window.__perf.chatFirst = performance.now() - start;
            observer.disconnect();
          }
        }).observe(document.getElementById('admin-ai-panel'), {
          childList: true, characterData: true, attributes: true, subtree: true,
        });
      }, { capture: true, once: true });
    });
    await page.locator('#admin-ai-panel .admin-ai-send').click();
    await page.waitForFunction(() => Number.isFinite(window.__perf.chatFirst), { timeout: 15000 });
    const firstText = await page.evaluate(() => window.__perf.chatFirst);
    const load = await readLoad(page, journal, pages[name]);
    record(name, 'journey', 1, {
      ...load, values: { errors: load.values.errors, unstubbed: load.values.unstubbed,
        chat_open: opened, chat_first_text: firstText },
    });
  });
}

function errorText(error) {
  return error?.message || String(error);
}

export default async function () {
  try {
    await withPage(pages.welcome, async (page) => {
      await page.goto(origin, { waitUntil: 'load' });
    });
  } catch (error) {
    recordFailure(`warm-up: ${errorText(error)}`);
    throw error;
  }
  if (journey) {
    try {
      await chatJourney();
    } catch (error) {
      recordFailure(`chat journey: ${errorText(error)}`);
    }
  } else {
    for (let run = 1; run <= runs; run++) {
      for (const [name, spec] of selectedPages) {
        try {
          await measurePage(name, spec, run);
        } catch (error) {
          recordFailure(`${name} run ${run}: ${errorText(error)}`);
        }
      }
    }
  }
  if (failures.length) throw new Error(failures.join('\n'));
}

export function handleSummary(data) {
  return { [__ENV.PERF_SUMMARY || 'browser-summary.json']: JSON.stringify(data) };
}
