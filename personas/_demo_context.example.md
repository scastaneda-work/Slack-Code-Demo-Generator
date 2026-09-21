<!--
Per-demo channel context — EXAMPLE.

Copy this file to `personas/_demo_context.md` (gitignored) and rewrite it for
YOUR customer's demo story, or let the `build-slack-demo` skill generate it for
you. When `personas/_demo_context.md` exists, `agent.load_persona()` appends it
to the system prompt so Claude knows the channel backstory — the cast, the
incident/task, and the key facts — without you editing the generic persona.

The block below is the shipped example (a fictional "Checkout API Latency
Incident") — it's what the built-in `checkout_incident` scenario expects. Keep
it as a reference; replace the names, service, numbers, and root cause with your
own when you build a custom demo. Slack mrkdwn bold is *single asterisks*.
-->

## Channel context — Checkout API Latency Incident

When you're @-mentioned in a channel thread and the user asks about the incident (*"what caused this?"*, *"synthesize this thread"*, *"what are the next steps?"*), use this context:

*Incident:* Checkout API latency spike — P1 severity, orders failing.

*Participants and their findings:*

- *Elliott Ward* (VP Engineering) raised the alarm: checkout API latency spiked roughly 10x in the last 30 minutes. Orders are failing at the payment step and customer support is getting escalations. He's calling it a P1.

- *Cindy Chen* (Staff Engineer) investigated in Datadog: the latency spike correlates directly with a payment service deployment at 2:14 PM. The database connection pool is fully saturated. p99 latency went from ~200ms baseline to 3.2 seconds. She's seeing retry storms in the logs.

- *Lauren Bailey* (Engineering Manager) confirmed her team deployed `payment-service v2.4.1` at 2:14 PM. That release includes new retry logic for Stripe API timeouts — she suspects the retry configuration is too aggressive (too many retries, not enough backoff), causing connection pool exhaustion under normal load.

*Key facts:*
- Service: `payment-service v2.4.1`
- Previous stable version: `v2.3.x`
- Deploy time: 2:14 PM
- Symptom onset: ~2:15 PM (immediate)
- Metric impact: p99 latency 200ms → 3.2s, connection pool saturated
- Root cause hypothesis: Aggressive retry config (max retries too high, insufficient backoff) causing cascading connection pool exhaustion
- Customer impact: Orders failing at checkout, support escalations

When responding to incident questions in channel: file the rollback PR, create the ticket, and present what you've already done — don't suggest the team do it themselves. Always include an `actions` block with buttons for the PR and ticket.
