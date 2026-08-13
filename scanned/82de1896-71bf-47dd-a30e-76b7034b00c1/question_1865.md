# Q1865: kamino_harvest_reward: reward transfer path can leak fee-on-transfer or intermediary drift [a-reward-state-that-is] [double-claim]

## Question
Can an unprivileged attacker use `kamino_harvest_reward` with a reward state that is freshly harvested vs stale in the same slot so `kamino_harvest_reward` leaks value through reward transfer handling drift, violating `Kamino reward harvest must claim exactly once from the correct obligation and deliver only to the canonical destination` and leading to `High: direct reward theft or repeated reward credit`? Focus specifically on one-time reward claim semantics across repeated harvest attempts.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/harvest_reward.rs` / `kamino_harvest_reward`
- Entrypoint: `kamino_harvest_reward`
- Attacker controls: a reward state that is freshly harvested vs stale in the same slot
- Exploit idea: Inspect reward paths that pass through temporary owners or fee-adjusted transfers before final delivery. Focus specifically on one-time reward claim semantics across repeated harvest attempts.
- Invariant to test: Kamino reward harvest must claim exactly once from the correct obligation and deliver only to the canonical destination
- Expected Immunefi impact: High: direct reward theft or repeated reward credit
- Fast validation: Fuzz reward amounts and token-account edge cases and assert net claimed, net transferred, and internal accounting always reconcile. Harvest once, replay immediately, and assert no second economic credit is created absent real external state change.
