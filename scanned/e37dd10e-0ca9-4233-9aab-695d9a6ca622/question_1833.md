# Q1833: kamino_harvest_reward: reward harvest decouples external claim and internal accounting [a-reward-state-that-is] [double-claim]

## Question
Can an unprivileged attacker use `kamino_harvest_reward` with a reward state that is freshly harvested vs stale in the same slot so `kamino_harvest_reward` records rewards internally without matching external claim success, breaking `Kamino reward harvest must claim exactly once from the correct obligation and deliver only to the canonical destination` and causing `High: direct reward theft or repeated reward credit`? Focus specifically on one-time reward claim semantics across repeated harvest attempts.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/harvest_reward.rs` / `kamino_harvest_reward`
- Entrypoint: `kamino_harvest_reward`
- Attacker controls: a reward state that is freshly harvested vs stale in the same slot
- Exploit idea: Audit the exact sequencing between external claim CPI and internal reward or balance updates. Focus specifically on one-time reward claim semantics across repeated harvest attempts.
- Invariant to test: Kamino reward harvest must claim exactly once from the correct obligation and deliver only to the canonical destination
- Expected Immunefi impact: High: direct reward theft or repeated reward credit
- Fast validation: Force claim-edge failures and assert internal balances, reward counters, and destination transfers remain unchanged. Harvest once, replay immediately, and assert no second economic credit is created absent real external state change.
