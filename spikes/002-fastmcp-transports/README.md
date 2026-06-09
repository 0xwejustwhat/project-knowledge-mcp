# 002: FastMCP stdio/http transports

## Question

Can FastMCP provide the Step 0 server shape over local stdio and loopback HTTP without adding custom protocol plumbing?

## Approach

`server.py` defines two simple tools: `health` and `validate_config`. `run_spike.py` calls the same server through:

- stdio subprocess transport; and
- HTTP transport bound to `127.0.0.1`.

## Run

```bash
poetry run python spikes/002-fastmcp-transports/run_spike.py
```

## Verdict: VALIDATED

### What worked

- FastMCP exposes typed tools with stable JSON-like responses.
- stdio works for local MCP clients.
- HTTP works when explicitly bound to loopback.

### Limitations

- Production bootstrap still needs generated client config and Docker/localhost binding guardrails.
- The MVP should standardize on `stdio` and `streamable-http/http` names supported by the installed FastMCP version.

### Recommendation for the real build

Use FastMCP for MCP protocol plumbing; keep exposure local-only by default and let setup generate client-specific config.
