# Q1805: kamino_harvest_reward: reward harvest pays an unvalidated recipient [replay-of-a-previously-valid] [double-claim]

## Question
Can an unprivileged attacker call `kamino_harvest_reward` with replay of a previously valid harvest context so `kamino_harvest_reward` harvests rewards but transfers them to an unvalidated recipient, violating `Kamino reward harvest must claim exactly once from the correct obligation and deliver only to the canonical destination` and causing `High: direct reward theft or repeated reward credit`? Focus specifically on one-time reward claim semantics across repeated harvest attempts.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/harvest_reward.rs` / `kamino_harvest_reward`
- Entrypoint: `kamino_harvest_reward`
- Attacker controls: replay of a previously valid harvest context
- Exploit idea: Probe destination account binding and ownership checks on every reward-transfer phase. Focus specifically on one-time reward claim semantics across repeated harvest attempts.
- Invariant to test: Kamino reward harvest must claim exactly once from the correct obligation and deliver only to the canonical destination
- Expected Immunefi impact: High: direct reward theft or repeated reward credit
- Fast validation: Provide attacker-controlled destinations and assert harvest rejects unless the canonical configured recipient is used. Harvest once, replay immediately, and assert no second economic credit is created absent real external state change.
