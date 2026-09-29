"""Shared inventory pressure with bounded stock protection for registered accounts."""
from collections import Counter
from contextlib import contextmanager
from datetime import timedelta

from ocpf_post import admission as old, local_store
from ocpf_post.state import state_dir


def path():
    return state_dir() / "admission-scopes.json"


INVENTORY_SAFETY_DAYS = 14


def _steady_policy():
    from ocpf_post import release_pacing
    from ocpf_post.portfolio import load_policy

    policy = load_policy(effective=False)
    return policy if release_pacing.enabled(policy) else None


def _account_daily_release(policy, provider: str, account: str) -> int:
    from ocpf_post.account_profiles import profile

    row = profile(provider, account)
    raw = (
        (row.get("policy") or {}).get("daily_target")
        if row is not None
        else (policy.get("providers") or {}).get(provider, {}).get("daily_target")
    )
    if type(raw) is not int or raw < 0:
        raise old.AdmissionError("Invalid saved daily release amount")
    return raw


class Budget:
    def __init__(self, now, apply):
        from ocpf_post.portfolio import delivery_candidates
        self.now, self.apply = now, apply
        self.policy = old.load_policy()
        self.steady_policy = _steady_policy()
        # Corrupt legacy pressure evidence is never silently discarded.
        legacy = old._load_state()
        self.state = local_store.read(path()) or {"schema_version": 1, "scopes": {}}
        if not isinstance(self.state.get("scopes"), dict) or any(not isinstance(r, dict) or r.get("mode") not in {"open", "paused"} for r in self.state.get("scopes", {}).values()):
            raise old.AdmissionError("Invalid scoped admission state")
        if "global_paused" in self.state and type(self.state["global_paused"]) is not bool:
            raise old.AdmissionError("Invalid global admission recovery state")
        for stamp in (legacy.get("observed_at"), self.state.get("observed_at")):
            if stamp and old._at(stamp) > now:
                raise old.AdmissionError("Admission clock regression")
        self.rows = delivery_candidates(now=now)
        self.total = len(self.rows)
        self.providers = Counter(r["provider"] for r in self.rows)
        self.accounts = Counter((r["provider"], r.get("account_id")) for r in self.rows)
        self.projects = Counter(r["project"] for r in self.rows)
        self._bindings = None
        self._active_work = None
        self.global_paused = self.state.get("global_paused", legacy.get("mode") == "paused" and self.total > self.policy["global_recovery_water"])
        if self.total <= self.policy["global_recovery_water"]:
            self.global_paused = False
        if self.total >= self.policy["global_high_water"]:
            self.global_paused = True

    def prime_shared_evidence(self):
        """Load registry bindings and active schedules once for route projections."""
        from ocpf_post.registry import load_registry
        from ocpf_post.scheduler import schedule_records
        from ocpf_post.schedule_semantics import ACTIVE_STATUSES
        if self._bindings is None:
            self._bindings = {
                (name, row["provider"], str(row["account_id"]))
                for name, value in load_registry()["projects"].items()
                for row in value.get("accounts", {}).values()
            }
        if self._active_work is None:
            self._active_work = [
                r for r in schedule_records() if r["status"] in ACTIVE_STATUSES
            ]
        return self

    def _reserve_stock(self, project, provider, account):
        """Count waiting AND reserved work; aliases cannot multiply the allowance.

        Callers already validate publishing authority. A protected admission also
        requires this exact project/account binding in the current registry.
        Snapshotting reservations is conservative if a publisher finishes later.
        """
        self.prime_shared_evidence()
        if (project, provider, account) not in self._bindings:
            return None
        return len({(r["campaign"], r["provider"], str(r.get("account_id")))
                    for r in self.rows + self._active_work
                    if r["provider"] == provider and str(r.get("account_id")) == account})

    def admit(self, project, provider, account, *, expires_at=None):
        key = provider + ":" + account
        from ocpf_post.account_profiles import unavailable
        try:
            inactive = unavailable(provider, account)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError):
            inactive = True
        if inactive:
            return {"admitted": False, "scope": key, "project": project, "reasons": ["account_unavailable"]}

        if self.steady_policy is not None:
            stock_before = self._reserve_stock(project, provider, str(account))
            if stock_before is None:
                return {
                    "admitted": False,
                    "scope": key,
                    "project": project,
                    "reasons": ["unregistered_account_binding"],
                }
            daily_release = _account_daily_release(self.steady_policy, provider, str(account))
            if daily_release <= 0:
                return {
                    "admitted": False,
                    "scope": key,
                    "project": project,
                    "reasons": ["daily_release_disabled"],
                }
            inventory_ceiling = daily_release * INVENTORY_SAFETY_DAYS
            rows = [
                r for r in self.rows
                if r["provider"] == provider and str(r.get("account_id")) == str(account)
            ]
            aged, _ = old._queue_ages(rows, now=self.now)
            expiry = sum(
                bool(r.get("expires_at"))
                and old._at(r["expires_at"]) <= self.now + timedelta(hours=self.policy["expiry_risk_hours"])
                for r in rows
            )
            diagnostic_pressure = []
            if self.providers[provider] >= self.policy["provider_high_water"][provider]:
                diagnostic_pressure.append("legacy_provider_high_water")
            if self.total >= self.policy["global_high_water"]:
                diagnostic_pressure.append("legacy_global_high_water")
            if self.projects[project] >= self.policy["project_high_water"]:
                diagnostic_pressure.append("legacy_project_high_water")
            if aged >= self.policy["aged_high_water"]:
                diagnostic_pressure.append("legacy_aged_high_water")
            if expiry >= self.policy["expiry_risk_high_water"]:
                diagnostic_pressure.append("legacy_expiry_risk_high_water")

            reasons = (
                ["account_inventory_safety_ceiling"]
                if stock_before >= inventory_ceiling
                else []
            )
            self.state["scopes"][key] = {
                "mode": "paused" if reasons else "open",
                "local_paused": bool(reasons),
                "metrics": {
                    "account_inventory": stock_before,
                    "account_inventory_safety_ceiling": inventory_ceiling,
                    "saved_daily_release": daily_release,
                    "safety_days": INVENTORY_SAFETY_DAYS,
                    "legacy_provider_inventory": self.providers[provider],
                    "legacy_global_inventory": self.total,
                    "aged": aged,
                    "expiry_risk": expiry,
                },
                "reasons": reasons[:],
                "diagnostic_pressure": diagnostic_pressure,
                "observed_at": old._stamp(self.now),
            }
            if not reasons:
                self.total += 1
                self.providers[provider] += 1
                self.accounts[(provider, str(account))] += 1
                self.projects[project] += 1
                self.rows.append({
                    "campaign": "pending-admission:" + str(self.total),
                    "project": project,
                    "provider": provider,
                    "account_id": str(account),
                    "expires_at": expires_at,
                })
            if self.apply:
                self.state["scopes"][key]["last_admission"] = {
                    "admitted": not reasons,
                    "protected": False,
                    "inventory_safety_ceiling": inventory_ceiling,
                    "stock_before": stock_before,
                    "saved_daily_release": daily_release,
                    "diagnostic_pressure": diagnostic_pressure,
                }
                self.state.update(
                    global_paused=False,
                    eligible_unreserved=self.total,
                    observed_at=old._stamp(self.now),
                    authority_model="deep_inventory_scheduler_limited",
                )
                local_store.write(path(), self.state)
            return {
                "admitted": not reasons,
                "scope": key,
                "project": project,
                "protected": False,
                "reserve_limit": inventory_ceiling,
                "stock_before": stock_before,
                "pressure_reasons": diagnostic_pressure,
                "reasons": reasons or ["within_deep_inventory_safety_ceiling"],
                "saved_daily_release": daily_release,
                "inventory_safety_days": INVENTORY_SAFETY_DAYS,
                "boundary": (
                    "Candidate inventory may remain deep under steady release pacing. "
                    "The scheduler, saved daily release amount, windows and hard daily "
                    "ceiling remain publication-rate authority."
                ),
            }

        p = self.policy
        rows = [r for r in self.rows if r["provider"] == provider and r.get("account_id") == account]
        aged, _ = old._queue_ages(rows, now=self.now)
        expiry = sum(bool(r.get("expires_at")) and old._at(r["expires_at"]) <= self.now + timedelta(hours=p["expiry_risk_hours"]) for r in rows)
        values = {"provider": self.providers[provider], "account": self.accounts[(provider, account)],
                  "aged": aged, "expiry_risk": expiry}
        high = {"provider": p["provider_high_water"][provider], "account": p["account_high_water"],
                "aged": p["aged_high_water"], "expiry_risk": p["expiry_risk_high_water"]}
        recovery = {"provider": p["provider_recovery_water"][provider], "account": p["account_recovery_water"],
                    "aged": p["aged_recovery_water"], "expiry_risk": p["expiry_risk_recovery_water"]}
        previous = self.state["scopes"].get(key, {})
        if previous.get("mode") not in {None, "open", "paused"}:
            raise old.AdmissionError("Invalid scoped admission mode")
        if "local_paused" in previous and type(previous["local_paused"]) is not bool:
            raise old.AdmissionError("Invalid local admission recovery state")
        local = ("account", "aged", "expiry_risk")
        prior_local = previous.get("local_paused", previous.get("mode") == "paused" and
                                   (any(k + "_high_water" in previous.get("reasons", []) for k in local) or
                                    "scope_recovery_not_reached" in previous.get("reasons", [])))
        local_paused = (prior_local and any(values[k] > recovery[k] for k in local)) or any(values[k] >= high[k] for k in local)
        paused = previous.get("mode") == "paused" and any(values[k] > recovery[k] for k in values)
        reasons = [k + "_high_water" for k in values if values[k] >= high[k]]
        if expires_at and old._at(expires_at) <= self.now + timedelta(hours=p["expiry_risk_hours"]) and expiry + 1 > high["expiry_risk"]:
            reasons.append("expiry_risk_high_water")
            local_paused = True
        if paused:
            reasons.append("scope_recovery_not_reached")
        self.state["scopes"][key] = {"mode": "paused" if reasons else "open", "local_paused": local_paused,
                                     "metrics": values, "reasons": reasons[:], "observed_at": old._stamp(self.now)}
        if self.global_paused or self.total + 1 > p["global_high_water"]:
            reasons.append("global_resource_ceiling")
        if self.projects[project] + 1 > p["project_high_water"]:
            reasons.append("project_high_water")
        pressure = reasons[:]
        reserve_stock = None
        protected = False
        reserve = min(p["account_inventory_reserve"], p["account_high_water"])
        if reasons and reserve and values["account"] < reserve:
            # Aggregate provider/project/global pressure may come entirely from
            # other accounts. Local ageing, expiry and account pressure still
            # hold their own recovery thresholds, including old persisted state.
            local_reasons = [k + "_high_water" for k in local if values[k] >= high[k]]
            if "expiry_risk_high_water" in reasons:
                local_reasons.append("expiry_risk_high_water")
            if local_paused:
                local_reasons.append("scope_recovery_not_reached")
            if not local_reasons:
                reserve_stock = self._reserve_stock(project, provider, account)
                protected = reserve_stock is not None and reserve_stock < reserve
                if protected:
                    reasons = []
        if not reasons:
            self.total += 1
            self.providers[provider] += 1
            self.accounts[(provider, account)] += 1
            self.projects[project] += 1
            self.rows.append({"campaign": "pending-admission:" + str(self.total), "project": project, "provider": provider, "account_id": account, "expires_at": expires_at})
            if self.total >= p["global_high_water"]:
                self.global_paused = True
        if self.apply:
            self.state["scopes"][key]["last_admission"] = {
                "admitted": not reasons, "protected": protected,
                "reserve_limit": reserve, "stock_before": reserve_stock,
                "pressure_reasons": pressure,
            }
            self.state.update(global_paused=self.global_paused, eligible_unreserved=self.total, observed_at=old._stamp(self.now))
            local_store.write(path(), self.state)
        return {"admitted": not reasons, "scope": key, "project": project,
                "protected": protected, "reserve_limit": reserve, "stock_before": reserve_stock,
                "pressure_reasons": pressure,
                "reasons": reasons or ["protected_account_inventory" if protected else "within_destination_budget"]}


def status():
    return local_store.read(path()) or {"schema_version": 1, "scopes": {}, "status": "not_observed"}


@contextmanager
def vault_budget(now):
    """Unavailable admission blocks additions, never vault withdrawal checks."""
    try:
        lock = local_store.locked(old.state_file())
        lock.__enter__()
    except (OSError, ValueError):
        yield None
        return
    try:
        try:
            budget = Budget(now, True)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError):
            budget = None
        yield budget
    finally:
        lock.__exit__(None, None, None)
