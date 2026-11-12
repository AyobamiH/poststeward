import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const workflow = readFileSync(new URL('../.github/workflows/ux-browser.yml', import.meta.url), 'utf8');
const browser = readFileSync(new URL('../tools/browser/verify.mjs', import.meta.url), 'utf8');
const routing = workflow.split('runs-on: >-')[1]?.split('timeout-minutes:')[0] || '';

test('trusted browser routing requires canonical repository and owner for PRs', () => {
  assert.match(routing, /github\.event_name == 'pull_request'/);
  assert.match(routing, /github\.event\.pull_request\.head\.repo\.full_name == github\.repository/);
  assert.match(routing, /github\.event\.pull_request\.user\.login == 'AyobamiH'/);
  assert.match(routing, /&& 'poststeward-release' \|\| 'ubuntu-latest'/);
  assert.doesNotMatch(workflow, /pull_request_target:/);
});

test('owner dispatch cannot select the privileged lane without main and explicit opt in', () => {
  assert.match(routing, /github\.event_name == 'workflow_dispatch' && inputs\.isolated_browser/);
  assert.match(routing, /github\.actor == 'AyobamiH'/);
  assert.match(routing, /github\.ref_type == 'branch'/);
  assert.match(routing, /github\.ref_name == 'main'/);
  assert.match(workflow, /isolated_browser:\s+description:[^\n]*\s+type: boolean\s+default: false/);
});

test('browser job retains read-only checkout, bounded execution and non-root browser selection', () => {
  assert.match(workflow, /permissions:\s+contents: read/);
  assert.equal((workflow.match(/persist-credentials: false/g) || []).length, 2);
  assert.match(workflow, /timeout-minutes: 15/);
  assert.match(workflow, /POSTSTEWARD_BROWSER_PREINSTALLED: \$\{\{ runner\.environment == 'self-hosted' \}\}/);
  assert.match(workflow, /test "\$\(id -u\)" -ne 0/);
  assert.match(workflow, /test -x \/usr\/local\/bin\/chromium/);
  assert.match(workflow, /npx --no-install playwright install --with-deps chromium/);
  assert.match(workflow, /POSTSTEWARD_BROWSER_EXECUTABLE: \$\{\{ runner\.environment == 'self-hosted'/);
  assert.match(browser, /\.\.\.\(browserExecutable \? \{ executablePath: browserExecutable \} : \{\}\)/);
  assert.doesNotMatch(workflow, /secrets\./);
});
