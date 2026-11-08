# Build and demo loop

1. **Goal** — Prove an isolated Hermes schedule delivered a valid synthetic JSON report.
2. **Done-check** — `./demo.py` verifies native registration, successful cron with stale output rejected, fresh incomplete JSON rejected, and two corrected cron runs accepted.
3. **Step prompt** — Resume the first incomplete scenario, run its producer, then collect its receipt. Never weaken or skip checks to make them pass.
4. **Verifier** — A registered Hermes CLI command reads job state and report evidence in a separate process. The demo independently asserts the producer status and verifier verdict.
5. **Stops** — Completion, 8 attempts per invocation, 120 seconds, or two attempts at a checkpoint without progress. Zero model calls and paid services.
6. **State** — Persist checkpoint and receipts under `.demo/`. Resume the first incomplete scenario.
7. **Supervision** — Follow stdout or read `.demo/local/checkpoint.json`. Ctrl-C pauses. Re-run to resume or supply a new directory for a clean demo.
