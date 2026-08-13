# Q1819: kamino_harvest_reward: reward harvest can be replayed against unchanged external state [duplicate-metas-that-alter-which] [double-claim]

## Question
Can an unprivileged attacker replay `kamino_harvest_reward` with duplicate metas that alter which obligation is interpreted as source so `kamino_harvest_reward` harvests or credits the same reward state twice, violating `Kamino reward harvest must claim exactly once from the correct obligation and deliver only to the canonical destination` and causing `High: direct reward theft or repeated reward credit`? Focus specifically on one-time reward claim semantics across repeated harvest attempts.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/harvest_reward.rs` / `kamino_harvest_reward`
- Entrypoint: `kamino_harvest_reward`
- Attacker controls: duplicate metas that alter which obligation is interpreted as source
- Exploit idea: Check one-time-use assumptions and post-harvest state refresh around reward collection. Focus specifically on one-time reward claim semantics across repeated harvest attempts.
- Invariant to test: Kamino reward harvest must claim exactly once from the correct obligation and deliver only to the canonical destination
- Expected Immunefi impact: High: direct reward theft or repeated reward credit
- Fast validation: Harvest once, replay immediately, and assert no second economic credit occurs unless external state actually changed. Harvest once, replay immediately, and assert no second economic credit is created absent real external state change.
