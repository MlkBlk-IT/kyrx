# Integrating KYRX with MCP clients

`kyrx init <client>` writes the KYRX block into a client's MCP configuration.
All outputs below are real, captured on Windows with KYRX 0.3.0 using `--path`
into a temp folder (no real client config was touched).

## What `kyrx init` guarantees

- Merges: other servers in the file are kept.
- Atomic write; a timestamped `.bak` backup is taken before any change.
- Idempotent: an up-to-date file is left alone, with no backup.
- `--print` writes nothing. `--path` overrides the client's config file.

```text
created: ...\w.json
unchanged: ...\w.json
updated: ...\w.json
backup: ...\w.json.20261115-001729.bak
```

Options: `--transport {stdio,http}`, `--host`, `--port`, `--token`,
`--data-dir`, `--path`, `--command`, `--print`, `--list`.

## stdio (default, local)

The client launches KYRX itself. Without `--command`, the block uses the bare
name `kyrx`, which only works if `kyrx` is on the client's `PATH`. From a venv,
pass the absolute path:

```powershell
kyrx init cursor --command C:\path\to\.venv\Scripts\kyrx.exe
```

Block written for cursor (`mcpServers`):

```json
{
  "mcpServers": {
    "kyrx": {
      "command": "kyrx",
      "args": ["serve", "--transport", "stdio"],
      "env": { "KYRX_DATA_DIR": "C:\\path\\to\\kyrx-data" }
    }
  }
}
```

## HTTP (remote or shared, bearer token)

1. `kyrx token` prints a 43-character token and ready-to-paste lines.
2. The server needs **both** `KYRX_AUTH_MODE=token` **and** `KYRX_AUTH_TOKEN`.
   Without them `kyrx serve --transport http` refuses to start.
3. Start it, then point the client at it with the same token:

```powershell
kyrx token --env                       # two KEY=VALUE lines
$env:KYRX_AUTH_MODE='token'; $env:KYRX_AUTH_TOKEN='<token>'
kyrx serve --transport http --host 127.0.0.1 --port 8765
kyrx init cursor --transport http --token <token>
```

Block written (`url` = `http://127.0.0.1:8765/mcp` by default):

```json
{ "mcpServers": { "kyrx": {
    "url": "http://127.0.0.1:8765/mcp",
    "headers": { "Authorization": "Bearer <token>" } } } }
```

Checked end to end: no token and a wrong token get HTTP 401; the right token
gets a session, 10 tools and a working `manifest` call. Only `127.0.0.1` was
tested. `--host`/`--port` of `init` must match those of `serve`.

## Single agent or fleet (coordination mode)

By default (`KYRX_MODE=auto`) KYRX finds out by itself: over stdio it starts on
the fast single-agent path and switches to full protection, for good, the first
time a second `agentId` claims a node. Over HTTP it starts fully protected. See
the README, section "Coordination mode".

To force a mode, set `KYRX_MODE` to `solo` or `fleet` in the server `env`.
`kyrx init` does not set it.

```json
"env": { "KYRX_DATA_DIR": "C:\\path\\to\\kyrx-data", "KYRX_MODE": "fleet" }
```

```text
claude mcp add --scope user --env KYRX_DATA_DIR=C:\path\to\kyrx-data --env KYRX_MODE=fleet kyrx -- kyrx serve --transport stdio
```

Leases are enforced in every mode. A step that runs longer than
`KYRX_LEASE_RUN_TTL_S` (20 s by default) without a `beat` loses its lease the
next time another node is claimed, and its `publish` is then refused with
`lease_not_held`. Call `beat` during long steps, or raise the TTL.

## Per-client notes

| Client | Key / format | Notes |
|---|---|---|
| `claude-desktop` | `mcpServers` | stdio only. `--transport http` exits 2: `claude_desktop_config.json declares local (stdio) servers only`. MSIX/Store install path: **not verified**. |
| `cursor` | `mcpServers` | `~/.cursor/mcp.json` |
| `vscode` | `servers` + `"type": "http"` | user-profile `mcp.json` |
| `cline` | `mcpServers`, `"type": "streamableHttp"` | VS Code extension file `cline_mcp_settings.json`. Cline CLI `~/.cline`: not supported. |
| `zed` | `context_servers` | Plain JSON only; a JSONC file is refused (exit 2). No Windows default: `kyrx init zed` exits 2 with `pass --path`. Default Zed path: **not verified**. |
| `claude-code` | runs `claude mcp add --scope user` | `--print` shows the command. **Never executed** against a real `claude`. |

`claude-code --print` output:

```text
claude mcp add --scope user --env KYRX_DATA_DIR=C:\path\to\kyrx-data kyrx -- kyrx serve --transport stdio
claude mcp add --scope user --transport http --header "Authorization: Bearer <token>" kyrx http://127.0.0.1:8765/mcp
```

## Not supported

- **ChatGPT, Gemini**: `kyrx init` only edits local files. As of 2026-11-15,
  third-party sources (not OpenAI's docs, not re-verified here) say ChatGPT
  reaches *remote* MCP servers via developer mode (beta), needing a public
  HTTPS endpoint with OAuth or no auth. KYRX's HTTP transport is bearer-only
  and untested with ChatGPT. Gemini: not checked.
- Claude Desktop over HTTP.
- Zed on Windows without `--path`.
- Cline CLI (`~/.cline`).
