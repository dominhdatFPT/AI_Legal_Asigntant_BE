const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { gunzipSync, gzipSync } = require('node:zlib');
const { spawnSync } = require('node:child_process');

for (const layout of ['template-only', 'both', 'api-only']) {
test(`ONLYOFFICE bootstrap survives flush-cache and restart: ${layout}`, (t) => {
  const root = fs.mkdtempSync(path.join(__dirname, '.tmp-legal-office-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const documentRoot = path.join(root, 'documentserver');
  const nginxRoot = path.join(root, 'nginx');
  const translations = path.join(documentRoot, 'sdkjs-plugins/{9DC93CDB-B576-4F0C-B55E-FCC9C48DD007}/translations');
  const api = path.join(documentRoot, 'web-apps/apps/api/documents/api.js');
  const common = path.join(nginxRoot, 'includes/http-common.conf');
  const staticConfig = path.join(nginxRoot, 'includes/ds-docservice.conf');
  fs.mkdirSync(translations, { recursive: true });
  fs.mkdirSync(path.dirname(api), { recursive: true });
  fs.mkdirSync(path.dirname(common), { recursive: true });
  fs.writeFileSync(path.join(translations, 'en.json'), '{"label":"English"}');
  fs.writeFileSync(path.join(translations, 'vi-VN.json'), '{"label":"Tiếng Việt"}');
  const apiSource = 'function createIframe(iframe) {\n  iframe.setAttribute("allow", "autoplay; camera;");\n  return iframe;\n}\n';
  if (layout !== 'api-only') {
    fs.writeFileSync(api + '.tpl', 'var cacheHash = "{{HASH_POSTFIX}}";\n' + apiSource);
    fs.chmodSync(api + '.tpl', 0o444);
  }
  if (layout !== 'template-only') {
    fs.writeFileSync(api, apiSource);
    fs.writeFileSync(api + '.gz', gzipSync('stale compressed API'));
  } else {
    assert.equal(fs.existsSync(api), false);
  }
  fs.writeFileSync(common, 'proxy_set_header Host $http_host;\n');
  fs.writeFileSync(staticConfig, 'location /web-apps {\n  add_header Cache-Control "public";\n}\nlocation /sdkjs-plugins {\n  add_header Cache-Control "public";\n}\n');
  const run = path.join(root, 'run.sh');
  // Simulate the supplied upstream cp/sed/rm lifecycle; ds ownership needs Linux smoke verification.
  fs.writeFileSync(run, [
    '#!/bin/sh',
    'set -eu',
    'api="$ONLYOFFICE_DOCUMENT_ROOT/web-apps/apps/api/documents/api.js"',
    'if [ -f "$api" ]; then',
    '  gzip -cd "$api.gz" > "$api.uncompressed"',
    '  cmp "$api.uncompressed" "$api"',
    '  rm -f "$api.uncompressed"',
    'fi',
    'if [ -f "$api.tpl" ]; then',
    '  cp -f "$api.tpl" "$api"',
    '  sed -i "s/{{HASH_POSTFIX}}/test-hash/g" "$api"',
    '  rm -f "$api.gz"',
    'fi',
    'printf "%s" "$JWT_SECRET"',
    '',
  ].join('\n'));
  const posix = (value) => value.replaceAll('\\', '/').replace(/^([A-Za-z]):/, (_, drive) => `/${drive.toLowerCase()}`);
  const bash = process.env.BASH_PATH || (process.platform === 'win32' ? 'C:/Program Files/Git/bin/bash.exe' : 'sh');
  const invoke = () => {
    const result = spawnSync(bash, [posix(path.resolve(__dirname, '../scripts/start-onlyoffice.sh'))], {
      encoding: 'utf8',
      env: { ...process.env,
        ONLYOFFICE_DOCUMENT_ROOT: posix(documentRoot),
        ONLYOFFICE_NGINX_ROOT: posix(nginxRoot),
        ONLYOFFICE_RUN_SCRIPT: posix(run),
        ONLYOFFICE_JWT_SECRET: 'test-office-secret',
      },
    });
    assert.equal(result.status, 0, result.stderr || result.error?.message);
    assert.equal(result.stdout, 'test-office-secret');
  };
  invoke();
  for (const folder of [translations, path.join(translations, 'helpers')]) {
    for (const locale of ['vi-VI', 'vi-VN']) {
      const target = path.join(folder, `${locale}.json`);
      const bytes = fs.readFileSync(target);
      JSON.parse(bytes);
      assert.deepEqual(gunzipSync(fs.readFileSync(`${target}.gz`)), bytes);
    }
  }
  assert.equal(JSON.parse(fs.readFileSync(path.join(translations, 'vi-VN.json'))).label, 'Tiếng Việt');
  assert.equal(JSON.parse(fs.readFileSync(path.join(translations, 'vi-VI.json'))).label, 'English');
  const context = vm.createContext({});
  vm.runInContext(fs.readFileSync(api, 'utf8'), context);
  const attributes = {};
  context.createIframe({
    setAttribute: (key, value) => { attributes[key] = value; },
    getAttribute: (key) => attributes[key],
  });
  assert.equal(attributes.allow, 'unload; autoplay; camera;');
  const files = [api, common, staticConfig];
  if (layout !== 'api-only') files.push(api + '.tpl');
  const checkApi = () => {
    const runtime = fs.readFileSync(api, 'utf8');
    assert.equal((runtime.match(/legal-office-unload/g) || []).length, 1);
    if (layout !== 'api-only') {
      const template = fs.readFileSync(api + '.tpl', 'utf8');
      assert.equal((template.match(/legal-office-unload/g) || []).length, 1);
      assert.ok(template.includes('{{HASH_POSTFIX}}'));
      assert.ok(!runtime.includes('{{HASH_POSTFIX}}'));
      assert.ok(runtime.includes('test-hash'));
      assert.equal(fs.existsSync(api + '.gz'), false);
    } else {
      assert.deepEqual(gunzipSync(fs.readFileSync(api + '.gz')), fs.readFileSync(api));
    }
  };
  checkApi();
  const firstRun = files.map((file) => fs.readFileSync(file, 'utf8'));
  assert.equal((firstRun[2].match(/Permissions-Policy/g) || []).length, 2);
  invoke();
  assert.deepEqual(files.map((file) => fs.readFileSync(file, 'utf8')), firstRun);
  checkApi();
});

}
