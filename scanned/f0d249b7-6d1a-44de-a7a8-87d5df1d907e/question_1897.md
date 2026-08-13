# Q1897: kamino_harvest_reward: reward destination selection can be influenced by optional accounts [a-reward-state-that-is] [double-claim]

## Question
Can an unprivileged attacker use `kamino_harvest_reward` with a reward state that is freshly harvested vs stale in the same slot so `kamino_harvest_reward` resolves reward destination from optional accounts in a way that violates `Kamino reward harvest must claim exactly once from the correct obligation and deliver only to the canonical destination` and causes `High: direct reward theft or repeated reward credit`? Focus specifically on one-time reward claim semantics across repeated harvest attempts.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/harvest_reward.rs` / `kamino_harvest_reward`
- Entrypoint: `kamino_harvest_reward`
- Attacker controls: a reward state that is freshly harvested vs stale in the same slot
- Exploit idea: Probe optional destination/owner accounts that may be under-validated because they are only used in the harvest terminal phase. Focus specifically on one-time reward claim semantics across repeated harvest attempts.
- Invariant to test: Kamino reward harvest must claim exactly once from the correct obligation and deliver only to the canonical destination
- Expected Immunefi impact: High: direct reward theft or repeated reward credit
- Fast validation: Provide alternate optional destinations and assert accepted harvests still deliver only to the canonical destination. Harvest once, replay immediately, and assert no second economic credit is created absent real external state change.
