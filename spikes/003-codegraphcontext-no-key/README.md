# 003: CodeGraphContext no-key reliability

## Question

Can `CodeGraphContext/CodeGraphContext` install, diagnose, index a small repo, and return code context without LLM keys, GPU, or a local model server?

## Approach

`run_spike.py` uses the `cgc` CLI with local KuzuDB:

- clear common API-key environment variables for child commands;
- run `cgc --db kuzudb --path .tmp/kuzu doctor`;
- index `fixture_repo`;
- run `stats`;
- run `find name Calculator` and `find name add`.

## Run

```bash
poetry install --extras codegraph
poetry run python spikes/003-codegraphcontext-no-key/run_spike.py
```

## Verdict: PARTIAL

### What worked

- Package `codegraphcontext==0.4.17` installs under Python 3.12.
- CLI diagnostics pass with local KuzuDB and no API key environment.
- It can index a small Python fixture repo and search for named elements.
- Supported language surface is broad enough to justify keeping the provider boundary.

### What did not work / limitations

- CLI output is human-oriented Rich text, not a clean stable JSON shape in the commands tested.
- The MVP needs an adapter that shields MCP tool responses from provider-specific output and can fall back to text retrieval.
- Docker image behavior still needs a dedicated image build in Step 1.

### Recommendation for the real build

Accept CodeGraphContext with limitations for the first provider boundary. Wire it behind `CodeContextProvider`; keep direct text fallback and actionable health warnings.
