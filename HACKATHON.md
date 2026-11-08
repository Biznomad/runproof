# RunProof hackathon entry

**Deadline** · Wednesday, November 11, 2026 at 5 p.m. Eastern. [Nous Research announcement](https://nousresearch.com/)

Repository · [Biznomad/runproof](https://github.com/Biznomad/runproof)

**Entry description** · RunProof checks whether a completed Hermes cron job produced a fresh JSON report with every required field populated. It emits a JSON verdict and a content hash for downstream checks.

## Demo script · 60–75 seconds

1. **0–10s** — “Hermes can record a successful job even if yesterday's report is still on disk.”
2. **10–30s** — Start `./demo.py --state-dir "$(mktemp -d)"`. Show cron completing and RunProof returning `stale_report`.
3. **30–45s** — Point out that the next producer wrote a fresh file but left out `summary`. Show `unpopulated_fields`.
4. **45–65s** — Show two PASS results and open `receipt-4.json`. Highlight the scheduled occurrence, execution window and content hash.
5. **65–75s** — “Use the JSON verdict and exit code to gate the workflow that consumes the report.”

## Local validation

Hermes 0.23.0 registered the CLI extension and ran four due one-shot schedules. The plugin validator passed on the installed plugin files. It flagged one advisory for the internal `cron.executions.latest_execution` import, which supplies the durable start and finish timestamps. Call it out in the catalog PR so maintainers can review that dependency.

## Entry checklist

1. Publish the standalone plugin repository after review and capture its full 40-character commit SHA.
2. Fill in the repo, SHA and maintainer in `catalog/runproof.yaml`. Run the Hermes catalog validator and open the catalog PR. [Catalog admission rules](https://github.com/NousResearch/hermes-agent/tree/main/plugin-catalog)
3. Record and review the demo. Post the approved video under 90 seconds on X and tag @NousResearch.
4. Submit the catalog PR and demo links via the [organizer’s form](https://t.co/jlduHHW3dr).
