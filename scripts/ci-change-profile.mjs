/** Fail-closed CI workload selection from the actual checked-out PR merge. */
import { execFileSync } from 'node:child_process';
import { appendFileSync, readFileSync } from 'node:fs';

const SAFE_DOC = /^(?:README\.md|CONTRIBUTING\.md|docs\/[a-zA-Z0-9_./-]+\.md)$/;
const OWNER = 'AyobamiH';

export function classifyPaths(paths) {
  if (!Array.isArray(paths) || paths.length === 0) return 'full';
  return paths.every(path =>
    typeof path === 'string' && !path.includes('..') &&
    SAFE_DOC.test(path) && !path.includes('//')
  ) ? 'docs' : 'full';
}

export function selectProfile({eventName, repository, actor, headRepo, prOwner,
  exactHeadMatch, files}) {
  if (eventName !== 'pull_request' || repository !== 'AyobamiH/poststeward' ||
      actor !== OWNER || headRepo !== repository || prOwner !== OWNER ||
      exactHeadMatch !== true) return 'full';
  return classifyPaths(files);
}

function git(args) {
  return execFileSync('git', args, {
    encoding: 'utf8', timeout: 5000, stdio: ['ignore','pipe','pipe'],
  }).trim();
}

export function detectProfile(env = process.env) {
  const fallback = {profile:'full', reason:'unverified_event_or_diff'};
  if (env.GITHUB_EVENT_NAME !== 'pull_request' || !env.GITHUB_EVENT_PATH) return fallback;
  try {
    const event = JSON.parse(readFileSync(env.GITHUB_EVENT_PATH, 'utf8'));
    const pr = event.pull_request;
    if (!pr?.head?.sha || !pr?.head?.repo?.full_name) return fallback;
    // Checkout on pull_request must be the GitHub merge ref with both
    // actual parents. If checkout depth or PR refs changed, fail FULL.
    const parents = git(['rev-list','--parents','-n','1','HEAD']).split(/\s+/);
    if (parents.length !== 3 || parents[2] !== pr.head.sha) return fallback;
    const output = git(['diff','--no-renames','--name-only',parents[1],parents[2]]);
    const files = output ? output.split(/\n+/) : [];
    const profile = selectProfile({
      eventName:env.GITHUB_EVENT_NAME, repository:env.GITHUB_REPOSITORY,
      actor:env.GITHUB_ACTOR, headRepo:pr.head.repo.full_name,
      prOwner:pr.user?.login, exactHeadMatch:true, files,
    });
    return {profile, reason:profile === 'docs' ? 'owner_pr_docs_only' : 'full_source_change'};
  } catch {
    return fallback;
  }
}

if (process.argv[1] && import.meta.url === new URL('file://' + process.argv[1]).href) {
  const result = detectProfile();
  if (process.argv.includes('--github-output') && process.env.GITHUB_OUTPUT) {
    appendFileSync(process.env.GITHUB_OUTPUT,
      'profile=' + result.profile + '\nreason=' + result.reason + '\n');
  }
  console.log('PostSteward CI profile=' + result.profile + ' (' + result.reason + ')');
}
