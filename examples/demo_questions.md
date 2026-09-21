# Claude AI demo questions

The 5 questions Claude AI should reliably handle in demos. Plain markdown:
one question per `- ` bullet, no sub-bullets, no headings between bullets.
The QA harness (`qa.py --vet-questions`) reads this file directly.

- Think through the Checkout API Latency incident step by step, then summarize it and the top 3 next steps as a Block Kit card.
- Reason through whether we should roll back or hotfix the payment-service regression, then give me your recommendation.
- Pull together a status card for the Checkout API Latency incident — owner, severity, last update.
- Draft a Slack message telling my team we're pushing the demo to next week.
- Explain what a vector database is in two short paragraphs.
