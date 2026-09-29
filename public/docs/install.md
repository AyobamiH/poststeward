# Install PostSteward

PostSteward is available as a hosted workspace **and** an installable agent/human
client. The client does not copy provider secrets out of the hosted control plane.

## macOS / Linux / WSL2

```bash
curl -fsSL --proto '=https' --tlsv1.2 https://poststeward.com/install.sh | bash
```

The installer is no-root and user-local. It requires Python 3.10+ and installs the
`poststeward` command under `~/.local/bin` by default.

Skip guided onboarding:

```bash
curl -fsSL --proto '=https' --tlsv1.2 https://poststeward.com/install.sh \
  | bash -s -- --no-onboard
```

Preview only:

```bash
curl -fsSL --proto '=https' --tlsv1.2 https://poststeward.com/install.sh \
  | bash -s -- --dry-run
```

Install an exact public repository revision:

```bash
curl -fsSL --proto '=https' --tlsv1.2 https://poststeward.com/install.sh \
  | bash -s -- --version <40-char-sha>
```

## Guided onboarding

```bash
poststeward onboard
```

The browser remains the **owner authority surface**:

1. sign in to PostSteward;
2. connect X, Threads and/or LinkedIn through provider OAuth;
3. verify the stable account/Page identity;
4. issue the smallest useful scoped, expiring agent grant;
5. return to the CLI and paste the token into the hidden prompt.

The CLI verifies the token with `workspace_status` before storing it locally with
user-only file permissions.

Provider credentials stay server-side. The CLI stores only the scoped agent grant.

## Humans and agents

Humans own identity, provider consent, grant issuance/revocation, recovery and
administrative authority.

Agents use the deterministic operation catalogue:

```bash
poststeward help --json
poststeward status
poststeward providers
poststeward receipts
poststeward invoke workspace_status '{}'
poststeward mcp
```

Every consequential operation keeps the existing PostSteward idempotency, owner-review,
receipt and ambiguous-effect boundaries.

## Providers

The same owner workspace supports:

- **X** — OAuth 2.0, stable identity, publish/readback where provider capability allows;
- **Threads** — OAuth, long-lived token handling, publishing and readback;
- **LinkedIn** — member publishing and separately reviewed organization/Page authority.

Application configuration is distinct from a connected account. If a provider card
says its OAuth application is unconfigured, no connection is inferred.

## Why the client is thin

PostSteward already owns the hosted provider-effect ledger and durable receipts.
Running a second independent provider-effect engine on the same destinations would
create split-brain authority.

The installable client therefore brings PostSteward to the shell/agent while the
hosted control plane remains authoritative for provider consequences. The proven
Post-Once Setup/Recovery and fail-closed ideas inform this client and future local
runtime work, but the original owner Post-Once installation is not copied into a
tester's machine.
