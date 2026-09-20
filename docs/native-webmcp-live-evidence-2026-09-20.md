# Native WebMCP live evidence — 20 September 2026

## Accepted scope

An authenticated supporting browser exposed PostSteward's native WebMCP registration and successfully executed harmless read-only workspace tools against production. The returned workspace identity matched the signed-in page and `workspace_status` returned exact production release `7a7f0fa698b1f21b01c5ad2971f13174d4249def`.

The native calls completed for:

- `workspace_status`;
- `accounts_list`;
- `projects_list`;
- `receipts_list`.

The bounded results agreed with the owner page: Free plan, publishing unpaused, two active OAuth connections across X and Threads, one X acceptance project and one `published_verified` X receipt. No credential material was returned.

## Conclusion

The `native_webmcp` gate is `live_verified` for authenticated native discovery and read-only execution. This closes the browser registration/execution question only. It does not prove a new provider connection, a new social publication, public signup, Advanced execution or any write-capable native operation.

HTTP and remote MCP remain supported agent surfaces. Native WebMCP is additive and depends on a browser that exposes the API.
