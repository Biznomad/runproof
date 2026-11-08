# RunProof hackathon entry

**Deadline** · Wednesday, November 11, 2026 at 5 p.m. Eastern. [Nous Research announcement](https://nousresearch.com/)

**Entry description** · RunProof blocks downstream work when a successful Hermes cron run leaves stale, missing, or invalid JSON. It releases the exact verified payload to a local outbox once per workflow slot and records what happened on every retry.

[Source repository](https://github.com/Biznomad/runproof) · [Hermes catalog PR](https://github.com/NousResearch/hermes-agent/pull/151045)

## Demo script · 60–75 seconds

1. **0–10s** — “A cron job can exit successfully and leave yesterday’s report in place.”
2. **10–30s** — Start `./demo.py --state-dir "$(mktemp -d)"`. Show Hermes completing the producer and RunProof blocking the stale report.
3. **30–45s** — Show the fresh but incomplete report getting blocked and the corrected report getting released.
4. **45–65s** — Show the next run skipping release. Open `replay.json` and point out the original execution ID and digest.
5. **65–75s** — “Downstream work reads one verified payload for this slot, even if the producer runs again.”

## Entry checklist

1. Record the synthetic demo and review the video.
2. Post the approved video under 90 seconds on X and tag @NousResearch.
3. Submit the catalog PR and demo links via the [organizer’s form](https://t.co/jlduHHW3dr).
