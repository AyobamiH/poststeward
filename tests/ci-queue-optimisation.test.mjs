import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

import { classifyPaths, selectProfile } from '../scripts/ci-change-profile.mjs';
import { analyseQueue, supersededCandidate } from '../scripts/ci-queue-analysis.mjs';

const workflow = readFileSync(new URL('../.github/workflows/ci.yml', import.meta.url), 'utf8');
const browser = readFileSync(new URL('../.github/workflows/ux-browser.yml', import.meta.url), 'utf8');

test('docs-only allowlist is narrow and fails closed', () => {
  assert.equal(classifyPaths(['README.md', 'docs/guide.md']), 'docs');
  assert.equal(classifyPaths([]), 'full');
  for (const file of ['runtime/src/worker.py', 'src/index.ts', '.github/workflows/ci.yml',
    'docs/build.js', 'docs/../src/index.ts', 'public/install.sh']) {
    assert.equal(classifyPaths(['README.md', file]), 'full', file);
  }
});

test('only authenticated owner PR merge-parent evidence permits docs-only profile', () => {
  const context = { eventName: 'pull_request', repository: 'AyobamiH/poststeward',
    actor: 'AyobamiH', headRepo: 'AyobamiH/poststeward', prOwner: 'AyobamiH',
    exactHeadMatch: true, files: ['docs/readme.md'] };
  assert.equal(selectProfile(context), 'docs');
  for (const changes of [{ actor: 'someone' }, { headRepo: 'other/fork' },
    { eventName: 'push' }, { exactHeadMatch: false }, { prOwner: 'other' }]) {
    assert.equal(selectProfile({ ...context, ...changes }), 'full');
  }
});

test('full Python 3.12 suite remains in protected verify and 3.10 matrix', () => {
  assert.equal((workflow.match(/-m unittest discover -s tests -p 'test_\*\.py'/g) || []).length, 2);
  assert.match(workflow, /matrix\.python.*3\.10/);
  assert.match(workflow, /test_poststeward_onboarding\.py/);
  assert.match(workflow, /test_setup_activation\.py/);
  assert.match(workflow, /poststeward-verification/);
});

test('draft PRs cannot consume CI and ready-for-review triggers the gates', () => {
  for (const content of [workflow,browser]) {
    assert.match(content, /types: \[opened, reopened, synchronize, ready_for_review\]/);
    assert.match(content, /if: github\.event_name != 'pull_request' \|\| github\.event\.pull_request\.draft == false/);
  }
});

test('owner-scoped concurrency preserves main and prevents cross-workflow cancellations', () => {
  for (const [content, prefix] of [[workflow, 'poststeward-verify'], [browser, 'ux-browser']]) {
    assert.match(content, new RegExp('group: ' + prefix + '-'));
    assert.match(content, /github\.event\.pull_request\.number \|\| github\.run_id/);
    assert.match(content, /cancel-in-progress: false/);
    assert.match(content, /github\.event\.pull_request\.head\.repo\.full_name == github\.repository/);
    assert.match(content, /github\.event\.pull_request\.user\.login == 'AyobamiH'/);
    assert.match(content, /github\.actor == 'AyobamiH'/);
  }
});

const project = { number: 24, state: 'open', head: { ref: 'feature', sha: 'b'.repeat(40),
  repo: { full_name: 'AyobamiH/poststeward' } }, user: { login: 'AyobamiH' },
  base: { ref: 'main' }, labels: [{name:'release-critical'}] };
const queued = { id: 12, name: 'Verify', event: 'pull_request', status: 'queued',
  head_branch: 'feature', head_sha: 'a'.repeat(40),
  head_repository: { full_name: 'AyobamiH/poststeward' },
  pull_requests: [{ number: 24 }], created_at: '2026-11-13T10:00:00Z' };

test('only genuinely superseded, same-repo owner queued PR checks are cancellable', () => {
  assert.equal(supersededCandidate(queued, project), true);
  for (const run of [
    { ...queued, head_sha: project.head.sha }, { ...queued, status:'in_progress' },
    { ...queued, event:'push' }, { ...queued, name:'Deploy requested staging revision' },
    { ...queued, pull_requests:[{number:23}] }, { ...queued, head_branch:'main' },
    { ...queued, head_repository:{full_name:'foreign/repo'} },
  ]) assert.equal(supersededCandidate(run, project), false);
  assert.equal(supersededCandidate(queued, { ...project, user: {login:'another'}}), false);
  assert.equal(supersededCandidate(queued, { ...project, state:'closed'}), false);
});

test('queue report distinguishes waiting, running, useful CI, and release priority', () => {
  const snapshot = {runs: [
    queued,
    { ...queued, id:13, head_sha:project.head.sha, created_at:'2026-11-13T10:01:00Z' },
    { ...queued, id:14, event:'push', status:'completed', conclusion:'success',
      created_at:'2026-11-13T10:02:00Z' },
  ], pulls:[project], jobs: {
    14:[{status:'completed', created_at:'2026-11-13T10:02:00Z',
      started_at:'2026-11-13T10:04:00Z',completed_at:'2026-11-13T10:09:00Z',conclusion:'success'}],
  }};
  const report=analyseQueue(snapshot, new Date('2026-11-13T10:10:00Z'));
  assert.equal(report.counts.queued,2);
  assert.equal(report.superseded_queued.length,1);
  assert.equal(report.superseded_queued[0].run_id,12);
  assert.equal(report.priority_queued.length,1);
  assert.equal(report.finished_jobs_with_timings,1);
  assert.equal(report.median_job_wait_minutes,2);
  assert.equal(report.median_job_execute_minutes,5);
});
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { detectProfile } from '../scripts/ci-change-profile.mjs';

test('real Git merge commit: docs-only classification is exact-head bound', () => {
  const temp = mkdtempSync(join(tmpdir(), 'poststeward-profile-'));
  const originalCwd = process.cwd();
  const git = (...args) => execFileSync('git', args, {
    cwd: temp, encoding: 'utf8', stdio: ['ignore','pipe','pipe'],
  }).trim();
  try {
    git('init', '-q', '-b', 'main');
    git('config', 'user.email', 'fixture@example.invalid');
    git('config', 'user.name', 'CI Fixture');
    mkdirSync(join(temp, 'docs'));
    writeFileSync(join(temp, 'README.md'), 'Base docs\n');
    writeFileSync(join(temp, 'docs', 'guide.md'), 'Guide\n');
    git('add', '.');
    git('commit', '-q', '-m', 'base');
    git('checkout', '-q', '-b', 'fixture');
    writeFileSync(join(temp, 'docs', 'guide.md'), 'Guide updated\n');
    git('commit', '-q', '-am', 'docs only');
    const head = git('rev-parse', 'HEAD');
    git('checkout', '-q', 'main');
    git('merge', '--no-ff', '-q', '-m', 'merge PR', 'fixture');
    const event = join(temp, 'event.json');
    writeFileSync(event, JSON.stringify({pull_request: {
      head: {sha:head,repo:{full_name:'AyobamiH/poststeward'}},
      user:{login:'AyobamiH'},
    }}));
    process.chdir(temp);
    const env = {GITHUB_EVENT_NAME:'pull_request', GITHUB_EVENT_PATH:event,
      GITHUB_REPOSITORY:'AyobamiH/poststeward',GITHUB_ACTOR:'AyobamiH'};
    assert.equal(detectProfile(env).profile, 'docs');
    writeFileSync(event, JSON.stringify({pull_request: {
      head:{sha:'a'.repeat(40),repo:{full_name:'AyobamiH/poststeward'}},
      user:{login:'AyobamiH'},
    }}));
    assert.equal(detectProfile(env).profile, 'full', 'mismatched merged head must fail closed');
    assert.equal(detectProfile({...env,GITHUB_EVENT_NAME:'push'}).profile, 'full');
  } finally {
    process.chdir(originalCwd);
    rmSync(temp, {recursive:true,force:true});
  }
});
