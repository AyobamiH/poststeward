/** Read-only queue evidence; optionally cancel only confirmed superseded owner PR runs. */
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';

const REPO = 'AyobamiH/poststeward';
const OWNER = 'AyobamiH';
const WORKFLOWS = new Set(['Verify','UX browser verification']);
const ACTIVE = new Set(['queued','waiting','pending','in_progress']);
const QUEUED = new Set(['queued','waiting','pending']);
const SENSITIVE = /^(?:ci:|integrate:|fix:)/i;

function minutesBetween(start, end) {
  const a = Date.parse(start || '');
  const b = Date.parse(end || '');
  return Number.isFinite(a) && Number.isFinite(b) && b >= a ? +( (b-a)/60000 ).toFixed(2) : null;
}
function median(values) {
  const data = values.filter(Number.isFinite).sort((a,b) => a-b);
  if (!data.length) return null;
  const centre = Math.floor(data.length / 2);
  return +(data.length % 2 ? data[centre] : (data[centre-1] + data[centre])/2).toFixed(2);
}
function matchingPr(run, pulls) {
  if (run.event !== 'pull_request' || !Array.isArray(pulls)) return null;
  const matches = pulls.filter(p => p?.state === 'open' &&
    p?.user?.login === OWNER && p?.head?.repo?.full_name === REPO &&
    p.head.ref === run.head_branch &&
    (!run.pull_requests?.length || run.pull_requests.some(r => r.number === p.number)));
  return matches.length === 1 ? matches[0] : null;
}

export function supersededCandidate(run, pr) {
  if (!run || !pr || run.event !== 'pull_request' || run.status !== 'queued' ||
      !WORKFLOWS.has(run.name) || !Number.isSafeInteger(run.id) ||
      pr.state !== 'open' || pr.user?.login !== OWNER ||
      pr.head?.repo?.full_name !== REPO ||
      run.head_repository?.full_name !== REPO ||
      run.head_branch !== pr.head?.ref || !Array.isArray(run.pull_requests) ||
      !run.pull_requests.some(r => r.number === pr.number) ||
      !/^[a-f0-9]{40}$/.test(String(run.head_sha || '')) ||
      !/^[a-f0-9]{40}$/.test(String(pr.head.sha || ''))) return false;
  return run.head_sha !== pr.head.sha;
}

export function analyseQueue({runs=[], pulls=[], jobs={}}, now=new Date()) {
  if (!Array.isArray(runs) || !Array.isArray(pulls)) throw new Error('Invalid queue snapshot');
  const counts = {queued:0, in_progress:0, completed:0, cancelled:0, other:0};
  const superseded_queued = [];
  const priority_queued = [];
  const queued_ages = [];
  const wait_times = [];
  const execute_times = [];
  const by_workflow = {};
  for (const run of runs) {
    if (!run || !Number.isSafeInteger(run.id)) continue;
    const status = run.status || '';
    if (QUEUED.has(status)) { counts.queued++; queued_ages.push(minutesBetween(run.created_at,now.toISOString())); }
    else if (status === 'in_progress') counts.in_progress++;
    else if (status === 'completed') { counts.completed++; if (run.conclusion === 'cancelled') counts.cancelled++; }
    else counts.other++;
    const workflow = String(run.name || '(unknown)');
    by_workflow[workflow] ??= {queued:0, in_progress:0, completed:0};
    if (QUEUED.has(status)) by_workflow[workflow].queued++;
    if (status === 'in_progress') by_workflow[workflow].in_progress++;
    if (status === 'completed') by_workflow[workflow].completed++;
    const pr = matchingPr(run,pulls);
    if (status === 'queued' && supersededCandidate(run,pr)) {
      superseded_queued.push({run_id:run.id, pr_number:pr.number,
        old_head:run.head_sha, current_head:pr.head.sha, workflow});
    }
    if (QUEUED.has(status) && pr && run.head_sha === pr.head.sha &&
        (pr.labels?.some(l => l.name === 'release-critical') || SENSITIVE.test(pr.title || ''))) {
      priority_queued.push({run_id:run.id, pr_number:pr.number, workflow,
        wait_minutes:minutesBetween(run.created_at,now.toISOString())});
    }
    for (const job of jobs[run.id] || []) {
      if (job.status !== 'completed' || !job.started_at || !job.completed_at) continue;
      const waited = minutesBetween(job.created_at || run.created_at,job.started_at);
      const executed = minutesBetween(job.started_at,job.completed_at);
      if (waited !== null) wait_times.push(waited);
      if (executed !== null) execute_times.push(executed);
    }
  }
  priority_queued.sort((a,b) => (b.wait_minutes ?? 0)-(a.wait_minutes ?? 0));
  superseded_queued.sort((a,b) => a.run_id-b.run_id);
  return {schema_version:1,repository:REPO,observed_at:now.toISOString(),
    counts, by_workflow, oldest_queued_minutes:queued_ages.filter(Number.isFinite).length ?
      Math.max(...queued_ages.filter(Number.isFinite)) : null,
    median_queued_age_minutes:median(queued_ages),
    sampled_finished_jobs:execute_times.length,
    finished_jobs_with_timings:execute_times.length,
    median_job_wait_minutes:median(wait_times),
    median_job_execute_minutes:median(execute_times),
    superseded_queued, priority_queued,
    priority_boundary:'GitHub does not offer a direct FIFO queue reorder; this report only identifies priority candidates.',
    safety_boundary:'No main, deployments, active jobs, foreign PRs, or same-head checks are cancellable.',
  };
}

function api(endpoint,method='GET') {
  const args=['api',endpoint];
  if (method !== 'GET') args.push('-X',method);
  const raw=execFileSync('gh',args,{
    encoding:'utf8',timeout:16000,maxBuffer:16*1024*1024,stdio:['ignore','pipe','pipe']});
  return raw.trim() ? JSON.parse(raw) : {};
}
function page(endpoint, maxPages=2) {
  const out=[];
  for(let p=1;p<=maxPages;p++) {
    const response=api(endpoint+(endpoint.includes('?')?'&':'?')+'per_page=100&page='+p);
    const data=Array.isArray(response)?response:response.workflow_runs || response.jobs || [];
    if(!Array.isArray(data)) throw new Error('Unexpected GitHub API response');
    out.push(...data);
    if(data.length<100) break;
  }
  return out;
}
function getLiveSnapshot() {
  const unique=new Map();
  for(const status of ['queued','in_progress','completed']) {
    const runs=page('repos/'+REPO+'/actions/runs?status='+status,
      status==='queued'?3:1);
    for(const run of runs) unique.set(run.id,run);
  }
  const pulls=page('repos/'+REPO+'/pulls?state=open',2);
  const jobs={};
  const finished=[...unique.values()].filter(r=>r.status==='completed' &&
    WORKFLOWS.has(r.name)).slice(0,10);
  for(const run of finished) {
    try { jobs[run.id]=page('repos/'+REPO+'/actions/runs/'+run.id+'/jobs',1); }
    catch { jobs[run.id]=[]; }
  }
  return {runs:[...unique.values()],pulls,jobs};
}

function parseArgs(argv) {
  const flags = new Set(argv);
  if(argv.some(x => x.startsWith('--') && ![
    '--json','--apply-cancel-stale','--expected-main','--snapshot'].includes(x)))
    throw new Error('Unknown queue-report flag');
  const position=name=>{const i=argv.indexOf(name);return i<0?null:argv[i+1];};
  return {json:flags.has('--json'),apply:flags.has('--apply-cancel-stale'),
    expectedMain:position('--expected-main'),snapshot:position('--snapshot')};
}
export function chooseCancellations(report) {
  return report.superseded_queued.slice(0,5);
}
function cancelStale(snap,report,expectedMain) {
  if (!/^[a-f0-9]{40}$/.test(expectedMain || '')) throw new Error('Apply requires --expected-main exact SHA');
  if (api('repos/'+REPO+'/branches/main').commit.sha !== expectedMain)
    throw new Error('Protected main changed; refuse cancellation');
  const changed=[];
  for (const candidate of chooseCancellations(report)) {
    // Fresh read of both objects prevents acting on stale discovery.
    const run=api('repos/'+REPO+'/actions/runs/'+candidate.run_id);
    const pr=api('repos/'+REPO+'/pulls/'+candidate.pr_number);
    if (!supersededCandidate(run,pr)) continue;
    const records=page('repos/'+REPO+'/actions/runs/'+run.id+'/jobs',1);
    if (!records.length || records.some(job => !['queued','waiting','pending'].includes(job.status)))
      continue;
    if (api('repos/'+REPO+'/branches/main').commit.sha !== expectedMain)
      throw new Error('Protected main changed during cancellation');
    api('repos/'+REPO+'/actions/runs/'+run.id+'/cancel','POST');
    changed.push({run_id:run.id,pr_number:pr.number,old_head:run.head_sha});
  }
  return changed;
}
if (process.argv[1] && import.meta.url === new URL('file://'+process.argv[1]).href) {
  try {
    const args=parseArgs(process.argv.slice(2));
    const snapshot=args.snapshot?JSON.parse(readFileSync(args.snapshot,'utf8')):getLiveSnapshot();
    const report=analyseQueue(snapshot);
    if(args.apply) {
      if(args.snapshot) throw new Error('Cannot cancel from a fixture snapshot');
      report.cancelled_stale_runs=cancelStale(snapshot,report,args.expectedMain);
    }
    console.log(JSON.stringify(report,null,2));
  } catch (error) {
    console.error('PostSteward queue report failed closed:',error.message);
    process.exitCode=1;
  }
}
