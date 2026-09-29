"""Tiny priority reserve for frozen learning inventory during ordinary backlog pressure.

The ordinary scoped admission budget remains authoritative. This wrapper can protect
at most two explicit learning items globally and one per project/provider/account
scope when ordinary inventory is paused. It never changes posting targets, provider
execution limits, schedules, receipts or account availability.
"""
from __future__ import annotations

from datetime import timedelta

from ocpf_post import admission as old, learning_equivalence, local_store
from ocpf_post.campaigns import builtin_manifest
from ocpf_post.scoped_admission import Budget, path as scoped_state_path

SUPPORTED_PROVIDERS = frozenset({"x", "threads"})
GLOBAL_LEARNING_RESERVE = 2
SCOPE_LEARNING_RESERVE = 1
SCOPE_COOLDOWN_HOURS = 24
_ALLOWED_PRESSURE = frozenset({
    "provider_high_water",
    "account_high_water",
    "aged_high_water",
    "scope_recovery_not_reached",
    "global_resource_ceiling",
    "project_high_water",
})


def _manifest_role(manifest):
    """Classify only frozen repository experiments; old/unfrozen copy is ordinary."""
    if not isinstance(manifest, dict) or manifest.get("payload_frozen") is not True:
        return None
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    arm = source.get("comparison_variant")
    source_id = str(source.get("source_id") or "")
    if source.get("type") != "repository_product_truth" or arm not in {"question", "practical"}:
        return None
    if source.get("sampling_predecessor"):
        return "challenger" if source_id.endswith("-" + arm) else None
    return "baseline" if source_id.endswith("-insight") else None


class LearningBudget(Budget):
    """Ordinary admission plus a fixed, fail-closed learning inventory reserve."""

    def _active_rows(self):
        if self._active_work is None:
            from ocpf_post.scheduler import schedule_records
            from ocpf_post.schedule_semantics import ACTIVE_STATUSES
            self._active_work = [row for row in schedule_records() if row.get("status") in ACTIVE_STATUSES]
        return self._active_work

    def _learning_stock(self, *, project=None, provider=None, account=None):
        rows = [*self.rows, *self._active_rows()]
        seen = set()
        count = 0
        for row in rows:
            campaign = str(row.get("campaign") or "")
            row_provider = str(row.get("provider") or "")
            row_account = str(row.get("account_id") or "")
            row_project = str(row.get("project") or "")
            identity = (campaign, row_provider, row_account)
            if not campaign or identity in seen:
                continue
            seen.add(identity)
            role = row.get("learning_role")
            if role not in {"baseline", "challenger"}:
                try:
                    role = _manifest_role(builtin_manifest(campaign))
                except (OSError, ValueError, KeyError, TypeError):
                    role = None
            if role not in {"baseline", "challenger"}:
                continue
            # A byte-identical, same-lineage verified historical effect already
            # satisfies this baseline. It must not occupy scarce admission stock
            # or be republished merely to create a newer campaign ID.
            if role == "baseline" and learning_equivalence.satisfied_baseline(
                campaign, row_provider, row_account
            ):
                continue
            if provider is not None and row_provider != provider:
                continue
            if account is not None and row_account != str(account):
                continue
            if project is not None:
                if not row_project:
                    try:
                        row_project = str(builtin_manifest(campaign).get("project") or "")
                    except (OSError, ValueError, KeyError, TypeError):
                        row_project = ""
                if row_project != project:
                    continue
            count += 1
        return count

    def _legacy_equivalence_matches_marker(self, protected, admitted):
        """Recognise the one v0.23-style marker that lacked campaign identity."""
        if protected.get("campaign") or protected.get("role") != "baseline":
            return False
        try:
            entries = learning_equivalence.load()["entries"]
        except (OSError, ValueError, KeyError, TypeError):
            return False
        matches = []
        for row in entries:
            if (
                row.get("baseline_project") != protected.get("project")
                or row.get("provider") != protected.get("provider")
                or str(row.get("account_id")) != str(protected.get("account_id"))
                or not row.get("recorded_at")
                or not row.get("baseline_campaign")
            ):
                continue
            try:
                recorded = old._at(row["recorded_at"])
                manifest = builtin_manifest(row["baseline_campaign"])
                source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
                source_admitted = old._at(source["admitted_at"])
            except (OSError, ValueError, KeyError, TypeError, old.AdmissionError):
                continue
            if recorded >= admitted and abs((source_admitted - admitted).total_seconds()) <= 1:
                matches.append(row)
        return len(matches) == 1

    def _cooldown_active(self, key, project):
        markers = self.state.get("learning_protected_scopes")
        if markers is None:
            return False
        if not isinstance(markers, dict):
            return True
        protected = markers.get(key) if isinstance(markers.get(key), dict) else {}
        if protected.get("project") != project or not protected.get("admitted_at"):
            return False
        try:
            admitted = old._at(protected["admitted_at"])
        except (ValueError, TypeError, old.AdmissionError):
            return True

        # A protected baseline that was later proven to be the exact same
        # already-verified social effect consumed no publication capacity. Its
        # anti-monopoly cooldown can therefore be released without widening the
        # reserve for genuinely scheduled or published learning work.
        campaign = str(protected.get("campaign") or "")
        if protected.get("role") == "baseline":
            if campaign and learning_equivalence.satisfied_baseline(
                campaign, str(protected.get("provider") or ""), str(protected.get("account_id") or "")
            ):
                return False
            if not campaign and self._legacy_equivalence_matches_marker(protected, admitted):
                return False
        return self.now - admitted < timedelta(hours=SCOPE_COOLDOWN_HOURS)

    def admit(self, project, provider, account, *, expires_at=None, learning_role=None, learning_campaign=None):
        ordinary = super().admit(project, provider, account, expires_at=expires_at)
        if ordinary.get("admitted"):
            if learning_role in {"baseline", "challenger"} and self.rows:
                self.rows[-1]["learning_role"] = learning_role
            return ordinary
        if learning_role not in {"baseline", "challenger"} or provider not in SUPPORTED_PROVIDERS:
            return ordinary
        reasons = set(ordinary.get("reasons") or [])
        if not reasons or not reasons <= _ALLOWED_PRESSURE:
            return ordinary
        if expires_at and old._at(expires_at) <= self.now + timedelta(hours=self.policy["expiry_risk_hours"]):
            return ordinary

        key = provider + ":" + str(account)
        scope_state = self.state.get("scopes", {}).get(key, {})
        metrics = scope_state.get("metrics") if isinstance(scope_state.get("metrics"), dict) else {}
        if int(metrics.get("expiry_risk", 0)) > int(self.policy["expiry_risk_recovery_water"]):
            return ordinary
        if self._cooldown_active(key, project):
            return {**ordinary,
                    "learning_protected": False,
                    "learning_role": learning_role,
                    "learning_reserve_reason": "learning_scope_cooldown"}

        # Protected inventory still requires the exact registered project/account binding.
        if self._reserve_stock(project, provider, str(account)) is None:
            return ordinary

        total_stock = self._learning_stock()
        scope_stock = self._learning_stock(project=project, provider=provider, account=str(account))
        if total_stock >= GLOBAL_LEARNING_RESERVE or scope_stock >= SCOPE_LEARNING_RESERVE:
            return {**ordinary,
                    "learning_protected": False,
                    "learning_role": learning_role,
                    "learning_stock_before": total_stock,
                    "learning_scope_stock_before": scope_stock,
                    "learning_reserve_limit": GLOBAL_LEARNING_RESERVE,
                    "learning_scope_reserve_limit": SCOPE_LEARNING_RESERVE,
                    "learning_reserve_reason": "learning_reserve_full"}

        pressure = ordinary.get("pressure_reasons") or ordinary.get("reasons") or []
        self.total += 1
        self.providers[provider] += 1
        self.accounts[(provider, str(account))] += 1
        self.projects[project] += 1
        self.rows.append({
            "campaign": str(learning_campaign or ("pending-learning-admission:" + str(self.total))),
            "project": project,
            "provider": provider,
            "account_id": str(account),
            "expires_at": expires_at,
            "learning_role": learning_role,
        })
        # Ordinary inventory stays paused. The protected class is deliberately tiny.
        self.global_paused = True
        result = {**ordinary,
                  "admitted": True,
                  "protected": False,
                  "learning_protected": True,
                  "learning_role": learning_role,
                  "learning_stock_before": total_stock,
                  "learning_scope_stock_before": scope_stock,
                  "learning_reserve_limit": GLOBAL_LEARNING_RESERVE,
                  "learning_scope_reserve_limit": SCOPE_LEARNING_RESERVE,
                  "learning_scope_cooldown_hours": SCOPE_COOLDOWN_HOURS,
                  "pressure_reasons": pressure,
                  "reasons": ["protected_learning_inventory"]}
        if self.apply:
            scope = self.state["scopes"].setdefault(key, {})
            scope["last_admission"] = {
                "admitted": True,
                "protected": False,
                "learning_protected": True,
                "learning_role": learning_role,
                "learning_stock_before": total_stock,
                "learning_scope_stock_before": scope_stock,
                "learning_reserve_limit": GLOBAL_LEARNING_RESERVE,
                "learning_scope_reserve_limit": SCOPE_LEARNING_RESERVE,
                "pressure_reasons": pressure,
            }
            markers = self.state.setdefault("learning_protected_scopes", {})
            if not isinstance(markers, dict):
                raise old.AdmissionError("Invalid learning-protected scope state")
            markers[key] = {
                "project": project,
                "provider": provider,
                "account_id": str(account),
                "role": learning_role,
                "campaign": str(learning_campaign or ""),
                "admitted_at": old._stamp(self.now),
            }
            self.state.update(global_paused=True, eligible_unreserved=self.total, observed_at=old._stamp(self.now))
            local_store.write(scoped_state_path(), self.state)
        return result
