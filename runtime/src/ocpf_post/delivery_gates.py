"""Read current local delivery gates without mutating the capacity trial."""
from datetime import datetime, timezone
import json

from ocpf_post.capacity_experiment import ACCOUNTS, delivery_gate


def report(*, now=None):
    now = now or datetime.now(timezone.utc)
    return {
        'schema_version': 1,
        'status': 'observed',
        'observed_at': now.isoformat(),
        'delivery_gates': {provider: delivery_gate(provider, now) for provider in ACCOUNTS},
        'boundary': 'Read-only local schedule, receipt and persisted readback evidence. No provider call, capacity mutation, retry, resend or ledger rewrite.',
    }


def main():
    print(json.dumps(report(), indent=2))


if __name__ == '__main__':
    main()
