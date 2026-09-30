"""Sequential, receipt-gated template sampling within existing source budgets."""
from datetime import timedelta

from ocpf_post import learning_equivalence, local_store
from ocpf_post.campaigns import campaign_ids, builtin_manifest
from ocpf_post.performance_review import publications
from ocpf_post.performance_feedback import enabled, metadata, path as feedback_path
from ocpf_post.engagement import at


def _feedback_observations(now):
    """Return only a fresh persisted feedback observation set, otherwise unknown.

    The feedback builder is the measurement authority: rows in this list have already
    passed the existing age-window, payload, availability, attribution and exposure
    checks. Sampling consumes that read model instead of duplicating metric rules.
    """
    try:
        report = local_store.read(feedback_path())
        observed_at = at(report['observed_at'])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    age_seconds = (now - observed_at).total_seconds()
    rows = report.get('observations')
    if (
        report.get('schema_version') != 1
        or report.get('enabled') is not True
        or not 0 <= age_seconds <= 24 * 60 * 60
        or not isinstance(rows, list)
        or any(not isinstance(row, dict) for row in rows)
    ):
        return None
    return rows


class SamplingBudget:
    def __init__(self, now, apply=False):
        self.now = now
        self.apply = apply
        try:
            self.active = enabled()
        except (ValueError, OSError):
            self.active = False  # A broken feedback policy cannot stop primary source supply.
        self.manifests = {cid: builtin_manifest(cid) for cid in campaign_ids()}
        self.publications = publications()
        self.feedback_observations = _feedback_observations(now)
        self.used = set()
        for manifest in self.manifests.values():
            source = manifest.get('source') or {}
            if not source.get('sampling_predecessor'):
                continue
            try:
                recent = now - timedelta(hours=24) < at(source['admitted_at'])
            except (ValueError, KeyError):
                recent = True
            if recent:
                for provider in manifest.get('providers', []):
                    self.used.add((manifest['project'], provider))

    def _baseline_manifests(self, item, provider, project):
        rows = []
        for campaign, manifest in self.manifests.items():
            source = manifest.get('source') or {}
            if (
                manifest.get('project') == project
                and provider in manifest.get('providers', [])
                and source.get('source_id') == item['predecessor_source_id']
            ):
                rows.append((campaign, manifest))
        return rows

    def _publication_from_attestation(self, row):
        key = (
            row.get('effect_campaign'),
            row.get('provider'),
            row.get('account_id'),
            row.get('effect_post_id'),
        )
        return self.publications.get(key)

    def _equivalent_candidate(self, item, provider, account, project):
        baselines = self._baseline_manifests(item, provider, project)
        if len(baselines) != 1:
            return None, ('predecessor_baseline_manifest_ambiguous' if len(baselines) > 1 else 'predecessor_not_published')
        campaign, manifest = baselines[0]
        source = manifest.get('source') or {}
        if source.get('source_sha') != item['sha']:
            return None, 'predecessor_source_revision_mismatch'

        attestation = learning_equivalence.lookup_predecessor(
            item['predecessor_source_id'], provider, account, project
        )
        if attestation:
            pub = self._publication_from_attestation(attestation)
            if not pub:
                return None, 'predecessor_equivalent_effect_unavailable'
            return (
                pub,
                source,
                pub['receipt'],
                learning_equivalence.editorial_from_attestation(attestation),
            ), None

        found = learning_equivalence.find_verified_equivalent(
            campaign, manifest, provider, account, self.publications
        )
        if found['status'] == 'ambiguous':
            return None, 'predecessor_equivalent_publication_ambiguous'
        if found['status'] != 'verified_equivalent':
            return None, 'predecessor_not_published'
        attestation = found['attestation']
        if self.apply:
            recorded = learning_equivalence.record(attestation, now=self.now)
            if recorded['status'] == 'effect_already_bound':
                return None, 'predecessor_equivalent_effect_already_bound'
            attestation = recorded['attestation']
        return (
            found['publication'],
            source,
            found['publication']['receipt'],
            learning_equivalence.editorial_from_attestation(attestation),
        ), None

    def _measurement_reason(self, receipt, meta):
        rows = self.feedback_observations
        if rows is None:
            return 'predecessor_measurement_state_unavailable'
        expected_identity = (
            receipt.get('campaign'),
            receipt.get('provider'),
            receipt.get('account_id'),
            receipt.get('post_id'),
        )
        editorial_fields = (
            'project', 'lane', 'variant', 'revision', 'topic', 'text_sha256',
            'template_version', 'comparison_variant',
        )
        for row in rows:
            identity = tuple(row.get(key) for key in ('campaign', 'provider', 'account_id', 'post_id'))
            if identity != expected_identity:
                continue
            editorial = row.get('editorial')
            if not isinstance(editorial, dict):
                continue
            if any(editorial.get(key) != meta.get(key) for key in editorial_fields):
                continue
            if row.get('target_age_hours') not in {24, 72, 168}:
                continue
            exposure = row.get('exposure')
            if type(exposure) not in (int, float) or exposure < 100:
                continue
            return None
        return 'predecessor_comparable_measurement_required'

    def reason(self, item, provider, account, project):
        if not self.active:
            return 'feedback_disabled'
        if (project, provider) in self.used:
            return 'sampling_daily_limit'

        candidates = []
        for key, pub in self.publications.items():
            manifest = self.manifests.get(key[0], {})
            source = manifest.get('source') or {}
            if key[1:3] != (provider, account) or source.get('source_id') != item['predecessor_source_id']:
                continue
            receipt = pub['receipt']
            candidates.append((pub, source, receipt, metadata(manifest, receipt)))

        if not candidates:
            equivalent, reason = self._equivalent_candidate(item, provider, account, project)
            if reason:
                return reason
            candidates = [equivalent]
        if len(candidates) != 1:
            return 'predecessor_publication_ambiguous'

        pub, source, receipt, meta = candidates[0]
        if source.get('source_sha') != item['sha']:
            return 'predecessor_source_revision_mismatch'
        if receipt.get('status') != 'published_verified' or receipt.get('readback_verified') is not True:
            return 'predecessor_not_verified'
        if not meta:
            return 'predecessor_missing_frozen_attribution'
        assigned = meta.get('comparison_variant')
        if assigned is None:
            return 'predecessor_missing_experiment_assignment'
        if assigned != item['comparison_variant']:
            return 'predecessor_experiment_arm_mismatch'
        if self.now - pub['at'] < timedelta(hours=48):
            return 'sampling_spacing'
        return self._measurement_reason(receipt, meta)

    def record(self, project, provider):
        # Conservative even if a destination alias later changes account.
        self.used.add((project, provider))
