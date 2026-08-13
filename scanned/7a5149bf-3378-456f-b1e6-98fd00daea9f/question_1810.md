# Q1810: kamino_harvest_reward: reward harvest can be replayed against unchanged external state [two-obligations-with-type-compatible] [source-destination]

## Question
Can an unprivileged attacker replay `kamino_harvest_reward` with two obligations with type-compatible reward contexts so `kamino_harvest_reward` harvests or credits the same reward state twice, violating `Kamino reward harvest must claim exactly once from the correct obligation and deliver only to the canonical destination` and causing `High: direct reward theft or repeated reward credit`? Focus specifically on binding the reward source position and final destination together under the same owner context.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/harvest_reward.rs` / `kamino_harvest_reward`
- Entrypoint: `kamino_harvest_reward`
- Attacker controls: two obligations with type-compatible reward contexts
- Exploit idea: Check one-time-use assumptions and post-harvest state refresh around reward collection. Focus specifically on binding the reward source position and final destination together under the same owner context.
- Invariant to test: Kamino reward harvest must claim exactly once from the correct obligation and deliver only to the canonical destination
- Expected Immunefi impact: High: direct reward theft or repeated reward credit
- Fast validation: Harvest once, replay immediately, and assert no second economic credit occurs unless external state actually changed. Mix source positions and destinations across users and assert harvest can neither pull from one nor pay another incorrectly.
