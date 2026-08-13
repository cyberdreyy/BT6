# Q1837: kamino_harvest_reward: reward harvest decouples external claim and internal accounting [replay-of-a-previously-valid] [double-claim]

## Question
Can an unprivileged attacker use `kamino_harvest_reward` with replay of a previously valid harvest context so `kamino_harvest_reward` records rewards internally without matching external claim success, breaking `Kamino reward harvest must claim exactly once from the correct obligation and deliver only to the canonical destination` and causing `High: direct reward theft or repeated reward credit`? Focus specifically on one-time reward claim semantics across repeated harvest attempts.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/harvest_reward.rs` / `kamino_harvest_reward`
- Entrypoint: `kamino_harvest_reward`
- Attacker controls: replay of a previously valid harvest context
- Exploit idea: Audit the exact sequencing between external claim CPI and internal reward or balance updates. Focus specifically on one-time reward claim semantics across repeated harvest attempts.
- Invariant to test: Kamino reward harvest must claim exactly once from the correct obligation and deliver only to the canonical destination
- Expected Immunefi impact: High: direct reward theft or repeated reward credit
- Fast validation: Force claim-edge failures and assert internal balances, reward counters, and destination transfers remain unchanged. Harvest once, replay immediately, and assert no second economic credit is created absent real external state change.
