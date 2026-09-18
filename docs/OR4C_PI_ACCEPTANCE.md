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

## Optional runtime context in a corpus case

A corpus case may include an optional `context` object. The harness forwards it unchanged to `lsa_local_analyze_command`. This is useful for validating domain-owned behavior that depends on neutral host metadata, such as recognized-speaker context.

Example:

```json
{
  "text": "monte mon retour de 3 dB",
  "expected": "ready",
  "effect": "write",
  "execute": false,
  "context": {
    "speaker": {
      "name": "Laurent",
      "confidence": 0.91,
      "backend": "resemblyzer"
    }
  }
}
```

The harness does not interpret the context. Each MCP remains responsible for its own domain semantics.

## Evidence to retain

Keep the two JSON reports with the tested Git commits and rack configuration. OR4C/OR4D should only be marked live-validated after the report is reviewed together with observed mixer/QLC behavior.


## OR4B4 XMSeries advanced acceptance

After the basic XMSeries MVP has passed live, use the dedicated template:

```bash
cp scripts/or4b4_xm_acceptance_corpus.example.json /tmp/or4b4-xm.json
vi /tmp/or4b4-xm.json
```

Replace `__CHANNEL__` with an exact safe channel name and `__BUS__` with an exact safe mix-bus name. The template covers absolute/relative percent, channel-to-bus routing, progressive ramps and delayed level actions.

Run the analysis-only pass first:

```bash
.venv/bin/python scripts/or4c_local_gateway_acceptance.py --env-file .env.offline --corpus /tmp/or4b4-xm.json --json-report /tmp/or4b4-xm-read.json
```

For live writes, enable `"execute": true` only for the individual cases currently under observation and add `--allow-writes`. Automation jobs return as soon as they are accepted by XMSeries-MCP, so confirm the final mixer state after the requested ramp/delay interval before marking that case live-validated. Do not run overlapping ramp/delay cases on the same target when validating final state.
