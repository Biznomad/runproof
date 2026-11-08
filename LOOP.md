# Build and demo loop

1. **Goal** — Prove an isolated Hermes schedule delivered a valid synthetic JSON report.
2. **Done-check** — `./demo.py` verifies native registration, successful cron with stale output rejected, fresh incomplete JSON rejected, and two corrected cron runs accepted.
3. **Step prompt** — Resume the first incomplete scenario, run its producer, then collect its receipt. Never weaken or skip checks to make them pass.
4. **Verifier** — A registered Hermes CLI command reads job state and report evidence in a separate process. The demo independently asserts the producer status and verifier verdict.
5. **Stops** — Completion, 8 attempts per invocation, 120 seconds, or two attempts at a checkpoint without progress. Zero model calls and paid services.
6. **State** — Persist checkpoint and receipts under `.demo/`. Resume the first incomplete scenario.
7. **Supervision** — Follow stdout or read `.demo/local/checkpoint.json`. Ctrl-C pauses. Re-run to resume or supply a new directory for a clean demo.

## Downstream release loop

1. **Goal** — Release one verified report per logical workflow slot, even when the producer is retried or operators race.
2. **Done-check** — A real Hermes demo and isolated replay of a captured independent completion report must block bad evidence, release a corrected report, and skip replay. A concurrent test must observe one outbox row and one release verdict.
3. **Step prompt** — Run one producer attempt for the same logical slot, verify the exact resulting bytes, then inspect the receipt and outbox independently. Never weaken or skip checks to pass.
4. **Verifier** — RunProof runs separately from the producer. The demo queries the SQLite ledger independently, counts releases, and hashes the released payload.
5. **Stops** — All scenarios passing, eight attempts per invocation, 120 seconds, or two attempts without progress at a checkpoint. Zero model calls and external downstream effects.
6. **State** — Checkpoints and shadow replay stay in ignored demo directories. The gate atomically commits verification receipts and the immutable payload in SQLite.
7. **Supervision** — Follow stdout and inspect the checkpoint and ledger. Ctrl-C pauses; resume the same demo directory or rerun the producer with the same release key.
