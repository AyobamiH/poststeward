"""Narrow, reviewed Cloudflare workflow evidence; never deploys or reads secrets."""
import base64
import hashlib
import re

from ocpf_post import replenisher as github

REPOSITORY = 'AyobamiH/opstruth-chatgpt-plugin'
WORKFLOW = '.github/workflows/deploy-cloudflare.yml'
# This exact reviewed workflow deploys OPSTRUTH_BUILD_COMMIT from GITHUB_SHA,
# then smoke-tests mcp.opstruth.io against that same expected commit.
REVIEWED_WORKFLOW_BLOB = 'dedc4e13ab54ee053a7ca648f2ec297ce338bb0a'
REQUIRED_STEPS = ('Deploy final Worker with stable commit identity', 'Smoke test production routes')


def observe_opstruth_deployment(sha, *, fetch=None, token=None):
    if not isinstance(sha, str) or not re.fullmatch('[a-f0-9]{40}', sha):
        raise ValueError('An exact source SHA is required')
    fetch = fetch or github._github_json
    token = token if token is not None else github._github_token()
    root = 'https://api.github.com/repos/' + REPOSITORY
    result = {'kind': 'github_actions_deployment', 'repository': REPOSITORY,
              'revision': sha, 'environment': 'production', 'status': 'unconfirmed',
              'scope': 'GitHub reports the reviewed deployment and production-smoke steps succeeded for this revision. Cloudflare control-plane state is not read; fresh application checks remain required.'}
    def get(path):
        return fetch(root + path, token=token)
    try:
        definition = get('/contents/' + WORKFLOW + '?ref=' + sha)
        raw = base64.b64decode(definition['content'].replace('\n', ''), validate=True)
        blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        if (definition.get('path') != WORKFLOW or definition.get('encoding') != 'base64'
                or len(raw) > 20000 or blob != REVIEWED_WORKFLOW_BLOB
                or definition.get('sha') != blob):
            raise ValueError('Deployment workflow definition needs a new review')
        data = get('/actions/runs?head_sha=' + sha + '&per_page=100')
        runs = data['workflow_runs']
        if not isinstance(runs, list) or len(runs) > 100 or any(not isinstance(r, dict) or r.get('head_sha') != sha for r in runs):
            raise ValueError('Invalid revision-scoped workflow response')
        candidates = [r for r in runs if r.get('path') == WORKFLOW
                      and r.get('head_branch') == 'main' and r.get('event') in ('push', 'workflow_dispatch')]
        if not candidates:
            return {**result, 'reason': 'No matching deployment run observed'}
        if any(type(r.get('id')) is not int for r in candidates):
            raise ValueError('Invalid workflow run identity')
        latest = max(candidates, key=lambda r: r['id'])
        run = get('/actions/runs/' + str(latest['id']))
        def identity(r):
            return tuple(r.get(k) for k in ('id', 'head_sha', 'path', 'head_branch', 'event', 'run_attempt', 'status', 'conclusion', 'updated_at'))
        if identity(run) != identity(latest):
            raise ValueError('Deployment run changed during observation')
        result.update(run_id=run['id'], run_attempt=run.get('run_attempt'),
                      url=f'https://github.com/{REPOSITORY}/actions/runs/{run["id"]}', workflow_blob=blob)
        if run.get('status') != 'completed' or run.get('conclusion') != 'success':
            return {**result, 'reason': 'Latest matching deployment run did not succeed'}
        attempt = run.get('run_attempt')
        if type(attempt) is not int or attempt < 1:
            raise ValueError('Deployment attempt identity is absent')
        data = get('/actions/runs/' + str(run['id']) + '/jobs?filter=latest&per_page=100')
        jobs = data['jobs']
        if not isinstance(jobs, list) or data.get('total_count') != len(jobs) or len(jobs) > 100:
            raise ValueError('Incomplete deployment jobs')
        selected = [j for j in jobs if j.get('name') == 'deploy']
        if len(selected) != 1:
            raise ValueError('Expected one deployment job')
        job = selected[0]
        if (job.get('run_id') != run['id'] or job.get('run_attempt') != attempt or job.get('head_sha') != sha
                or job.get('status') != 'completed' or job.get('conclusion') != 'success'):
            raise ValueError('Deployment job identity or outcome mismatch')
        steps = [[s for s in job['steps'] if s.get('name') == name] for name in REQUIRED_STEPS]
        if any(len(s) != 1 or s[0].get('status') != 'completed' or s[0].get('conclusion') != 'success' for s in steps):
            raise ValueError('Required deployment and smoke steps did not both succeed')
        if not (type(steps[0][0].get('number')) is int and type(steps[1][0].get('number')) is int
                and steps[0][0]['number'] < steps[1][0]['number']):
            raise ValueError('Deployment must precede smoke verification')
        if identity(get('/actions/runs/' + str(run['id']))) != identity(run):
            raise ValueError('Deployment run changed during observation')
        return {**result, 'status': 'observed', 'job_id': job['id'],
                'completed_at': job.get('completed_at'), 'successful_steps': list(REQUIRED_STEPS)}
    except (ValueError, KeyError, TypeError, AttributeError, OSError, github.ReplenisherError) as exc:
        return {**result, 'status': 'unavailable', 'error_type': type(exc).__name__}
