"""Credential-free refresh history; consent publishing state is not inferred."""
from datetime import datetime, timezone
import hashlib
from ocpf_post import local_store
from ocpf_post.state import state_dir
from ocpf_post.engagement import at


def record_refresh(credential, *, success, error_type=None, now=None):
    now = now or datetime.now(timezone.utc)
    identity = hashlib.sha256((credential['client_id'] + ':' + credential['refresh_token']).encode()).hexdigest()
    path = state_dir() / 'google-refresh-observations.json'
    # Observation failure must not turn a successful OAuth refresh into failure.
    try:
        with local_store.locked(path):
            data = local_store.read(path) or {'schema_version': 1, 'credentials': {}}
            row = data['credentials'].setdefault(identity, {'successes': 0, 'failures': 0})
            row['successes' if success else 'failures'] += 1
            row.update(last_attempt_at=now.isoformat(), last_result='success' if success else 'unavailable')
            if success:
                row.setdefault('first_success_at', now.isoformat())
                row['last_success_at'] = now.isoformat()
            else:
                row['last_error_type'] = error_type
            data['current_credential'] = identity
            local_store.write(path, data)
    except (OSError, ValueError):
        pass


def report():
    data = local_store.read(state_dir() / 'google-refresh-observations.json')
    row = data.get('credentials', {}).get(data.get('current_credential'), {})
    span = ((at(row['last_success_at']) - at(row['first_success_at'])).total_seconds() / 86400
            if row.get('first_success_at') and row.get('last_success_at') else None)
    return {'schema_version': 1, 'status': 'observed' if row else 'not_observed',
            'refresh': row, 'observed_success_span_days': span,
            'refresh_beyond_seven_days_observed': span is not None and span > 7,
            'oauth_publishing_status': 'not_observed',
            'boundary': 'Same-credential successful refresh observations only. No credential values. Cloud Console consent publishing state requires separate owner evidence; refresh success does not prove permanent access.'}
