"""Scoped, expiring application observations; never publishing authority."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
import base64
import hashlib
import json
import re

from ocpf_post.bounded_http import json_request
from ocpf_post import onboarding as ob
from ocpf_post.source_evidence import inspect_source, _configured
from ocpf_post.state import state_dir, write_private_json


def field(value, path):
    for part in path.split('.'):
        if not isinstance(value, dict) or part not in value:
            raise ValueError('Expected response field is absent')
        value = value[part]
    return value


def check(path, *, sha, apply=False, expected_sha256=None, reader=json_request, source_reader=inspect_source, deployment_reader=None, now=None):
    config, digest = ob.read_input(path, apply=apply, expected_sha256=expected_sha256)
    required = {'schema_version', 'project', 'repository', 'environment', 'url', 'revision_field', 'assertions', 'ttl_minutes'}
    ob._object(config, required | {'behaviour_adapter', 'deployment_adapter'}, required, 'application evidence policy')
    ob._slug(config['project'], 'project'); ob._text(config['environment'], 'environment')
    if type(config['schema_version']) is not int or config['schema_version'] != 1 or not re.fullmatch('[a-f0-9]{40}', sha):
        raise ValueError('Expected schema version 1 and an exact commit SHA')
    profile, _ = _configured(config['project'])
    if config['repository'] != profile['repository']:
        raise ValueError('Repository does not match registered project source')
    url = urlsplit(config['url'])
    if (url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment
            or url.port not in (None, 443)):
        raise ValueError('Application URL must be HTTPS without credentials, query, fragment or custom port')
    if type(config['ttl_minutes']) is not int or not 1 <= config['ttl_minutes'] <= 1440:
        raise ValueError('Observation TTL must be 1 to 1440 minutes')
    assertions = config['assertions']
    if not isinstance(assertions, dict) or not 1 <= len(assertions) <= 16:
        raise ValueError('Provide 1 to 16 explicit JSON field assertions')
    for key in [config['revision_field'], *assertions]:
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.]{0,100}', key):
            raise ValueError('Invalid JSON field path')
    if any(type(v) not in (str, int, bool) or (isinstance(v, str) and len(v) > 200) for v in assertions.values()):
        raise ValueError('Assertion values must be bounded scalars')
    adapter = config.get('behaviour_adapter')
    if adapter is not None and (adapter != 'opstruth-verifier-identity-v1'
            or config['url'] != 'https://mcp.opstruth.io/health'
            or config['repository'] != 'AyobamiH/opstruth-chatgpt-plugin'):
        raise ValueError('Behaviour adapter is not supported for this exact runtime')
    deployment_adapter = config.get('deployment_adapter')
    if deployment_adapter is not None and (deployment_adapter != 'opstruth-cloudflare-workflow-v1'
            or adapter != 'opstruth-verifier-identity-v1' or config['environment'] != 'production'):
        raise ValueError('Deployment adapter is not supported for this exact runtime')
    result = {'schema_version': 1, 'project': config['project'], 'repository': config['repository'],
              'revision': sha, 'environment': config['environment'], 'url': config['url'],
              'input_sha256': digest, 'assertions': assertions, 'status': 'preview',
              'behaviour_adapter': adapter, 'deployment_adapter': deployment_adapter,
              'boundary': 'Only the configured assertions at this endpoint and time. No full feature, business or publishing-authority claim.'}
    if not apply:
        return result
    now = now or datetime.now(timezone.utc)
    result.update(observed_at=now.isoformat(), expires_at=(now + timedelta(minutes=config['ttl_minutes'])).isoformat())
    try:
        evidence = source_reader(config['project'], sha=sha)
        if evidence['source']['sha'] != sha:
            raise ValueError('Source revision mismatch')
        result['deployment_records'] = [d for d in evidence['deployments']['records']
            if d.get('sha') == sha and d.get('environment') == config['environment']
            and (d.get('latest_status') or {}).get('state') == 'success']
        if deployment_adapter:
            from ocpf_post.deployment_evidence import observe_opstruth_deployment
            result['deployment_workflow'] = (deployment_reader or observe_opstruth_deployment)(sha)
        response = reader(config['url'])
        result['revision_matches'] = field(response, config['revision_field']) == sha
        results = {key: type(field(response, key)) is type(expected) and field(response, key) == expected
                   for key, expected in assertions.items()}
        result['assertion_results'] = results
        if adapter and result['revision_matches'] and all(results.values()):
            result['behaviour'] = verifier_identity(reader)
            after = reader(config['url'])
            result['revision_matches'] = field(after, config['revision_field']) == sha
            results['behaviour_adapter'] = result['behaviour']['passed']
        result['status'] = ('observed' if result['deployment_records'] else 'application_observed_deployment_unconfirmed') if result['revision_matches'] and all(results.values()) else 'failed'
        if (result['status'] == 'application_observed_deployment_unconfirmed'
                and result.get('deployment_workflow', {}).get('status') == 'observed'):
            result['status'] = 'application_observed_workflow_deployment_matched'
    except Exception as exc:
        result.update(status='unavailable', error_type=type(exc).__name__)
    # Store only configured assertions/results, never the full endpoint payload.
    with ob._import_lock('application-evidence'):
        write_private_json(state_dir() / 'application-evidence' / (config['project'] + '.json'), result)
    return result


def verifier_identity(reader):
    """One fixed read-only MCP tool, not a general tool-execution interface."""
    request = {'jsonrpc': '2.0', 'id': 'post-once-identity-check', 'method': 'tools/call',
               'params': {'name': 'opstruth_get_verifier_identity', 'arguments': {}}}
    reply = reader('https://mcp.opstruth.io/mcp', data=json.dumps(request).encode(),
                   headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
    result = reply.get('result', {})
    identity = result.get('structuredContent', {})
    pem = identity.get('publicKeyPem', '')
    if not isinstance(pem, str) or len(pem) > 2000:
        raise ValueError('Invalid public identity response')
    der = base64.b64decode(''.join(pem.replace('-----BEGIN PUBLIC KEY-----', '').replace('-----END PUBLIC KEY-----', '').split()), validate=True)
    fingerprint = hashlib.sha256(der).hexdigest()
    valid_der = len(der) == 44 and der[:12] == bytes.fromhex('302a300506032b6570032100')
    passed = (reply.get('jsonrpc') == '2.0' and reply.get('id') == request['id'] and not reply.get('error')
              and not result.get('isError') and identity.get('schema') == 'opstruth.verifier-identity.v1'
              and identity.get('algorithm') == 'Ed25519' and identity.get('changedState') is False
              and valid_der and identity.get('signerFingerprint') == 'sha256:' + fingerprint
              and identity.get('doneStateSignerFingerprint') == fingerprint)
    return {'adapter': 'opstruth-verifier-identity-v1', 'passed': passed,
            'signer_fingerprint': 'sha256:' + fingerprint if passed else None,
            'scope': 'Live read-only MCP identity retrieval and public-key fingerprint consistency; not signer trust, signature verification or a complete repository-verification transaction.'}
