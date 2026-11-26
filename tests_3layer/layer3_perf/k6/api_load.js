import http from 'k6/http';
import exec from 'k6/execution';
import { check } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';

const { load } = JSON.parse(open('../budgets.json'));
const scenario = __ENV.SCENARIO || 'baseline';
if (!['baseline', 'stress', 'spike', 'soak', 'domains'].includes(scenario)) {
  throw new Error(`Unknown load scenario: ${scenario}`);
}
const policy = load[scenario];
const tokens = __ENV.PERF_TOKENS ? JSON.parse(open(__ENV.PERF_TOKENS)).citizens : [];
const soakMinutes = Number(__ENV.SOAK_MINUTES || load.soak.minutes);
if (!Number.isInteger(soakMinutes) || soakMinutes < load.soak.min_minutes) {
  throw new Error(`SOAK_MINUTES must be an integer >= ${load.soak.min_minutes}`);
}

const requests = new Counter('perf_requests');
const rejected = new Counter('perf_429');
const errors = new Rate('perf_errors');
const unexpected = new Counter('perf_unexpected');
const latency = new Trend('perf_latency', true);
const mix = [
  { name: 'procedures', path: '/api/procedures?page_size=20&domain=land', weight: 30, group: 'cached', service: 'procedure' },
  { name: 'procedure_detail', path: '/api/procedures/DD-001', weight: 15, group: 'cached', service: 'procedure' },
  { name: 'domains', path: '/api/procedures/domains', weight: 10, group: 'domains', service: 'procedure' },
  { name: 'user', path: '/api/users/me', weight: 10, group: 'db_read', service: 'user' },
  { name: 'notifications', path: '/api/notifications?limit=10', weight: 10, group: 'db_read', service: 'notification' },
  { name: 'terms', path: '/api/consultations/terms', weight: 10, group: 'db_read', service: 'consultation' },
  { name: 'specializations', path: '/api/lawyers/specializations', weight: 5, group: 'db_read', service: 'lawyer' },
  { name: 'applications', path: '/api/procedures/applications/me', weight: 5, group: 'db_read', service: 'procedure' },
  { name: 'reference_catalog', path: '/api/contracts/reference-catalog', weight: 5, group: 'cached', service: 'contract' },
];
const addresses = Array.from({ length: load.virtual_ips }, (_, index) =>
  `10.90.${Math.floor(index / 250)}.${index % 250 + 1}`);

const seconds = (number) => `${number}s`;
const startTime = seconds(load.warmup_seconds);
const warmup = {
  executor: 'constant-arrival-rate', rate: load.warmup_rate, timeUnit: '1s',
  duration: startTime, preAllocatedVUs: 20, maxVUs: 100, gracefulStop: '0s',
  tags: { phase: 'warmup' },
};
const shared = {
  timeUnit: '1s', startTime, preAllocatedVUs: 100, maxVUs: 600,
  gracefulStop: seconds(load.request_max_ms / 1000), tags: { phase: 'measure' },
};
const thresholds = {
  'perf_requests{phase:measure}': [`count>=${load.minimum_samples}`],
  'perf_429{phase:measure}': [`count==${load.rejections}`],
  'perf_latency{phase:measure}': [`max<=${load.request_max_ms}`],
};
// Stress and spike may degrade past the knee by design; their dropped_iterations stay in the summary only.
if (load.dropped_iterations_scenarios.includes(scenario)) {
  thresholds.dropped_iterations = [`count==${load.dropped_iterations}`];
}
let measured;

if (scenario === 'stress') {
  const stageSeconds = policy.seconds / policy.targets.length;
  measured = {
    ...shared, executor: 'ramping-arrival-rate', startRate: load.warmup_rate,
    stages: policy.targets.map((target) => ({ target, duration: seconds(stageSeconds) })),
  };
  for (const target of policy.targets) {
    thresholds[`perf_latency{phase:measure,window:${target}}`] = [`max<=${load.request_max_ms}`];
    if (target <= policy.failure_to_rps) {
      thresholds[`perf_errors{phase:measure,window:${target}}`] = [`rate<${policy.failure_rate}`];
    }
  }
} else if (scenario === 'spike') {
  measured = {
    ...shared, executor: 'ramping-arrival-rate', startRate: policy.low_rate,
    stages: [
      { target: policy.low_rate, duration: seconds(policy.baseline_seconds) },
      { target: policy.peak_rate, duration: seconds(policy.up_seconds) },
      { target: policy.peak_rate, duration: seconds(policy.peak_seconds) },
      { target: policy.low_rate, duration: seconds(policy.down_seconds) },
      { target: policy.low_rate, duration: seconds(policy.recovery_seconds) },
    ],
  };
  thresholds['perf_errors{phase:measure,window:spike}'] = [`rate<${policy.failure_rate}`];
  thresholds['perf_errors{phase:measure,window:recovery}'] = ['rate==0'];
  const baseline = Number(__ENV.PERF_BASELINE_P95 || load.baseline.p95_ms);
  // The absolute floor keeps a few-ms baseline from turning ~150 recovery samples into a noise test.
  const recovery = Math.max(baseline * policy.recovery_ratio, baseline + policy.recovery_floor_ms);
  thresholds['perf_latency{phase:measure,window:recovery}'] = [`p(95)<=${recovery}`];
} else {
  measured = {
    ...shared, executor: 'constant-arrival-rate', rate: policy.rate,
    duration: scenario === 'soak' ? `${soakMinutes}m` : seconds(policy.seconds),
  };
  if (scenario === 'baseline') {
    thresholds['perf_latency{phase:measure}'].push(`p(95)<=${policy.p95_ms}`, `p(99)<=${policy.p99_ms}`);
    thresholds['http_req_failed{phase:measure}'] = [`rate<${policy.failure_rate}`];
    thresholds['checks{phase:measure}'] = [`rate==${load.checks}`];
    for (const [group, budget] of Object.entries(policy.groups)) {
      thresholds[`perf_latency{phase:measure,api_group:${group}}`] = [`p(95)<=${budget}`];
    }
    for (const service of new Set(mix.map((endpoint) => endpoint.service))) {
      for (const phase of ['warmup', 'measure']) {
        thresholds[`perf_requests{phase:${phase},service:${service}}`] = ['count>0'];
      }
    }
  } else if (scenario === 'domains') {
    thresholds['perf_latency{phase:measure}'].push(`p(99)<=${policy.p99_ms}`);
    thresholds['perf_unexpected{phase:measure}'] = [`count==${policy.unexpected_errors}`];
  } else {
    thresholds['perf_errors{phase:measure}'] = [`rate==${policy.failure_rate}`];
    for (const window of ['first', 'last']) {
      thresholds[`perf_latency{phase:measure,window:${window}}`] = [`max<=${load.request_max_ms}`];
    }
  }
}

export const options = {
  scenarios: { warmup, measure: measured }, thresholds,
  summaryTrendStats: ['avg', 'min', 'p(50)', 'p(95)', 'p(99)', 'max', 'count'],
  maxRedirects: 0,
};

export function setup() {
  if (tokens.length !== load.users || tokens.some((token) => !token)) {
    throw new Error(`PERF_TOKENS must contain ${load.users} citizen JWTs from the test stack`);
  }
}

function windowTag(age) {
  if (scenario === 'stress') {
    const index = Math.min(policy.targets.length - 1, Math.floor(age / (policy.seconds / policy.targets.length)));
    return String(policy.targets[index]);
  }
  if (scenario === 'spike') {
    if (age < policy.baseline_seconds) return 'before';
    if (age < policy.baseline_seconds + policy.up_seconds + policy.peak_seconds + policy.down_seconds) return 'spike';
    return 'recovery';
  }
  if (scenario === 'soak') {
    if (age < 60) return 'first';
    if (age >= (soakMinutes - 1) * 60) return 'last';
  }
  return 'steady';
}

function sampleEndpoint() {
  if (scenario === 'domains') return mix[2];
  let draw = Math.random() * 100;
  for (const endpoint of mix) {
    draw -= endpoint.weight;
    if (draw < 0) return endpoint;
  }
  throw new Error('The nine endpoint weights must total 100');
}

export default function () {
  const endpoint = sampleEndpoint();
  const phase = exec.scenario.name;
  const age = (Date.now() - exec.scenario.startTime) / 1000;
  const tags = {
    phase, window: phase === 'measure' ? windowTag(age) : 'warmup',
    // `group` is a k6 system tag; a custom tag with that name can be overwritten.
    name: endpoint.name, api_group: endpoint.group, service: endpoint.service,
  };
  const start = Date.now();
  const response = http.get(load.origin + endpoint.path, {
    headers: {
      Authorization: `Bearer ${tokens[Math.floor(Math.random() * tokens.length)]}`,
      'CF-Connecting-IP': addresses[Math.floor(Math.random() * addresses.length)],
    },
    tags, timeout: `${load.request_max_ms}ms`,
  });
  let valid = false;
  let intentional = false;
  if (response.status === 200 || response.status === load.domains.overload_status) {
    try {
      const body = response.json();
      valid = response.status === 200 && body.error === null && body.data != null;
      intentional = scenario === 'domains' && response.status === load.domains.overload_status
        && body.error?.code === 'PROCEDURE_DB_UNAVAILABLE';
    } catch (_) {
      // Invalid JSON is a failed check and an unexpected response.
    }
  }
  check(response, { 'successful API envelope or intentional pool saturation': () => valid || intentional }, tags);
  requests.add(1, tags);
  rejected.add(Number(response.status === 429), tags);
  errors.add(!valid, tags);
  unexpected.add(Number(!valid && !intentional), tags);
  latency.add(Date.now() - start, tags);
}

export function handleSummary(data) {
  return { [__ENV.PERF_SUMMARY || 'load-summary.json']: JSON.stringify({ ...data, scenario }) };
}
