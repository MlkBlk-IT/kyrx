# REQUIREMENTS — KYRX 0.2.0

## Runtime
- Python **3.11+** (`requires-python = ">=3.11"`). Run and tested only on 3.14.7.
- OS tested: Windows 11 Pro 10.0.26300. Other OS: not tested (native wheels: `wasmtime`, `blake3`, `orjson`).
- No Docker needed.

## Dependencies (`pyproject.toml`) — range / installed in `.venv314`
| Package | Range | Installed |
|---|---|---|
| mcp | >=2.3,<3 | 2.3.0 |
| pydantic | >=2.14,<3 | 2.14.2 |
| pydantic-settings | >=2.16,<3 | 2.16.2 |
| httpx | >=0.28,<1 | 0.28.1 |
| orjson | >=3.13,<4 | 3.13.0 |
| blake3 | >=1.0,<2 | 1.0.11 |
| wasmtime | >=50,<51 | 50.0.1 |

Optional extra `tokenizer`: `tiktoken>=0.8,<1` (installed: 0.14.0).
Extra `dev`: mcp[cli], pytest, pytest-asyncio, pytest-cov, hypothesis, ruff, mypy.

## Install
```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

## Supported MCP clients (`kyrx init`)
| Client | Transport | Note |
|---|---|---|
| claude-desktop | stdio | HTTP refused |
| cursor | stdio, http | |
| vscode | stdio, http | key `servers` |
| cline (VS Code extension) | stdio, http | `streamableHttp` |
| zed | stdio, http | plain JSON only; `--path` required on Windows |
| claude-code | stdio, http | runs `claude mcp add`; never executed here |

## Not supported
- ChatGPT, Gemini (no local config file for `kyrx init`; see docs/integration.md)
- Claude Desktop over HTTP
- Zed on Windows without `--path`
- Cline CLI (`~/.cline`)
