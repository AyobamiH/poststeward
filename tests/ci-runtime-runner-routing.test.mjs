import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

const workflow = readFileSync(fileURLToPath(new URL('../.github/workflows/ci.yml', import.meta.url)), 'utf8');
function lane(job) {
  const start = workflow.indexOf('  ' + job + ':\n');
  assert.ok(start >= 0, 'missing ' + job);
  const begin = workflow.indexOf('    runs-on: >-\n', start);
  assert.ok(begin >= 0, 'missing protected runner selection for ' + job);
  const end = "  && 'poststeward-verification' || 'ubuntu-latest' }}";
  return workflow.slice(begin, workflow.indexOf(end, begin) + end.length);
}
test('automatic verification runs once for a PR, but still checks protected main pushes', () => {
  assert.match(workflow, /on:\n  push:\n    branches: \[main\]\n  pull_request:/);
  assert.match(workflow, /  workflow_dispatch:/);
});

test('runtime matrix shares the exact trust predicate used by the primary verifier', () => {
  const verify = lane('verify');
  const runtime = lane('runtime');
  assert.equal(runtime, verify);
  assert.match(runtime, /github\.event_name == 'workflow_dispatch' && inputs\.self_hosted_verify/);
  assert.match(runtime, /github\.event\.pull_request\.head\.repo\.full_name == github\.repository/);
  assert.match(runtime, /github\.repository == 'AyobamiH\/poststeward'/);
  assert.match(runtime, /github\.actor == 'AyobamiH'/);
  assert.match(runtime, /github\.event_name == 'push' && github\.ref_type == 'branch'/);
  assert.match(runtime, /github\.event\.pull_request\.user\.login == 'AyobamiH'/);
  assert.doesNotMatch(runtime, /vars\.POSTSTEWARD_VERIFY_BRANCH/);
  assert.match(runtime, /'poststeward-verification' \|\| 'ubuntu-latest'/);
});
test('runtime uses pinned Python setup and both supported Python versions', () => {
  const runtime = workflow.slice(workflow.indexOf('  runtime:\n'));
  assert.match(runtime, /matrix:\n\s+python: \["3\.10", "3\.12"\]/);
  assert.match(runtime, /actions\/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065/);
  assert.doesNotMatch(runtime, /setup-python@v[0-9]+/);
});

test('runtime installs into a fresh virtual environment, never modifying an externally managed toolcache', () => {
  const runtime = workflow.slice(workflow.indexOf('  runtime:\n'));
  assert.match(runtime, /python -m venv "\$RUNNER_TEMP\/poststeward-runtime-matrix"/);
  assert.match(runtime, /poststeward-runtime-matrix\/bin\/python" -m pip install -e/);
  assert.match(runtime, /poststeward-runtime-matrix\/bin" >> "\$GITHUB_PATH"/);
  assert.doesNotMatch(runtime, /--break-system-packages/);
});


test('bounded Node test file concurrency retains the complete protected verification suite', () => {
  const pkg = JSON.parse(readFileSync(fileURLToPath(new URL('../package.json', import.meta.url)), 'utf8'));
  assert.match(pkg.scripts.test, /--test-concurrency=2(?=\s|$)/);
  assert.match(pkg.scripts.test, /tests\/\*\.test\.\*/);
  assert.match(pkg.scripts.verify, /&& npm test(?:\s|$)/);
  assert.match(workflow, /- run: npm run verify/);
});
