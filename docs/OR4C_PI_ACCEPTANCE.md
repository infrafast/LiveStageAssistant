# OR4C Raspberry Pi deterministic gateway acceptance

This procedure validates the LiveStageAssistant Local deterministic path against the real MCP processes on the rack. It does not replace unit/CI coverage.

## Safety

The harness is read-safe by default:

- read plans execute so their real MCP response can be measured;
- write plans never execute unless the corpus contains `"execute": true` **and** the command line includes `--allow-writes`;
- the saved MCP configuration is not changed;
- STDIO children receive the Local gateway environment overlay only in memory;
- the harness records whether a new known local-LLM/inference process appears during the run.

## Prepare a rack corpus

Copy the example:

```bash
cp scripts/or4c_acceptance_corpus.example.json /tmp/or4c-rack.json
vi /tmp/or4c-rack.json
```

Replace `__REMPLACER_PAR_UNE_CAPTION_EXACTE__` with a real QLC+ Virtual Console button caption. Add representative XMSeries read/write commands that match the currently connected mixer inventory.

Keep live writes with `"execute": false` for the first pass.

## Read-only first pass

```bash
python scripts/or4c_local_gateway_acceptance.py --env-file .env.offline --corpus /tmp/or4c-rack.json --json-report /tmp/or4c-read.json
```

Expected acceptance points:

- at least one compatible `lsa-command-gateway/v1` server is discovered;
- the intended routed MCP is the only claimant for routed commands;
- unrouted commands never execute when multiple gateways claim them;
- read plans execute successfully;
- write plans are reported as skipped unless explicitly allowed;
- no new Ollama/llama-server/LocalAI process appears;
- the JSON report contains per-command analysis/total latency and p50/p95 summaries.

## Explicit live-write pass

After reviewing the read-only report, set `"execute": true` only on the exact writes you want to test, then run:

```bash
python scripts/or4c_local_gateway_acceptance.py --env-file .env.offline --corpus /tmp/or4c-rack.json --allow-writes --json-report /tmp/or4c-live.json
```

The harness never retries a write. A failed, stale, ambiguous or rejected plan remains failed.

## Evidence to retain

Keep the two JSON reports with the tested Git commits and rack configuration. OR4C/OR4D should only be marked live-validated after the report is reviewed together with observed mixer/QLC behavior.
