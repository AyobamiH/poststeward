# Root-key inventory rehearsal

Root replacement is distinct from changing an envelope derivation version. This tool rehearses every credential in a supplied, release-pinned snapshot without modifying the deployed service.

## Inventory contract

Supply the complete workspace ID inventory, one full record snapshot per workspace, and every D1 github_installations row. Exhaust source pagination before setting the three completeness flags. Include inactive accounts and stale GitHub installations: they may still retain encrypted material. An active GitHub refresh lease blocks rehearsal.

The module maps account secrets to workspace:alias, OAuth refresh secrets to workspace:oauth:alias, and GitHub user credentials to workspace:github:installation_id. Unknown credential-bearing record families, duplicate records, missing workspaces and invalid contexts block the run.

Run Node 24 with scripts/rehearse-root-inventory.mjs INPUT_JSON PRIVATE_OUTPUT_JSON. Supply POSTSTEWARD_CURRENT_ROOT and POSTSTEWARD_NEXT_ROOT only through the protected execution environment. The output file is created exclusively with mode 0600; it contains encrypted replacements and expected original-envelope digests. Standard output contains counts and hashes, never credential plaintext.

## Evidence and cutover boundary

The tool rewraps and verifies all supplied credentials before producing a plan. It proves next-root readability and old-root rejection for the replacements. It does not independently prove the completeness flags, fetch a live inventory, apply writes, replace deployment secrets, or approve deletion of the previous root.

Before a live cutover, a protected operator integration must compare a fresh live inventory with this snapshot, fence credential changes, stage replacements with compare-and-swap checks against the expected digests, and verify the deployment's complete read path. That protected live integration remains required; this rehearsal is not permission to run bulk SQL or overwrite credentials during normal traffic.

Keep the previous root and recovery backups until all live and recoverable credentials have been accounted for. Restored historical envelopes still need their original root.

Primary reference: [Google Cloud KMS key rotation](https://docs.cloud.google.com/kms/docs/key-rotation), which distinguishes rotation from re-encryption and requires checking whether old key versions remain in use.
