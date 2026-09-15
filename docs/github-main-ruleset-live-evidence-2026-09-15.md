# GitHub main ruleset live evidence — 15 September 2026

This record closes the `github_main_ruleset` production governance gate. It records the real server-side repository policy after the reviewed canonical ruleset from PR #79 was applied by the repository owner.

## Owner apply receipt

The owner ran the checked-in `governance:apply` command from current `main` using their authenticated GitHub CLI session and the exact confirmation `APPLY_POSTSTEWARD_MAIN_RULESET`.

The command returned:

- `changed: true`
- `ready: true`
- action `created`
- ruleset id `23461973`
- required status context `verify`
- GitHub Actions integration id `15368`
- approving reviews required `0`

The immediately following checked-in `governance:check` returned `ready: true`.

## Independent GitHub readback

PostSteward then independently read GitHub ruleset `23461973` through the GitHub API. The live repository response showed:

- name `PostSteward main protection`;
- target `branch`, source `AyobamiH/poststeward`, enforcement `active`;
- condition `~DEFAULT_BRANCH`;
- deletion protection;
- non-fast-forward protection;
- required signed commits;
- required linear history;
- pull-request rule with zero required approving reviews, no last-push approval, required review-thread resolution and squash as the only allowed merge method;
- strict required status check `verify`, pinned to GitHub Actions integration `15368`;
- no bypass actors and `current_user_can_bypass: never`.

This is server-side enforcement, not an inference from repository files or merged-PR provenance.

## Boundary

The zero-review requirement is deliberate for the current single-maintainer repository: requiring the maintainer to approve their own change would not create independent review. The policy instead enforces PR-only changes, exact CI, signed commits, linear history, thread resolution, squash-only merges and no direct bypass. If a second trusted maintainer is added later, review-count policy should be revisited as a new governance decision.

No provider authority, payment, publication, recovery action, production deployment or customer state was changed to produce this evidence.
