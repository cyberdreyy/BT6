# Q1877: kamino_harvest_reward: harvest path trusts stale external reward state [same-slot-harvest-followed-by] [double-claim]

## Question
Can an unprivileged attacker call `kamino_harvest_reward` with same-slot harvest followed by withdraw or another harvest investigation path so `kamino_harvest_reward` harvests against stale external reward state and miscredits value, breaking `Kamino reward harvest must claim exactly once from the correct obligation and deliver only to the canonical destination` and causing `High: direct reward theft or repeated reward credit`? Focus specifically on one-time reward claim semantics across repeated harvest attempts.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/harvest_reward.rs` / `kamino_harvest_reward`
- Entrypoint: `kamino_harvest_reward`
- Attacker controls: same-slot harvest followed by withdraw or another harvest investigation path
- Exploit idea: Where reward freshness or prerequisite refresh exists, ensure the harvested state matches the refreshed state exactly. Focus specifically on one-time reward claim semantics across repeated harvest attempts.
- Invariant to test: Kamino reward harvest must claim exactly once from the correct obligation and deliver only to the canonical destination
- Expected Immunefi impact: High: direct reward theft or repeated reward credit
- Fast validation: Manipulate refresh prerequisites and assert harvest rejects unless the external reward state was refreshed and bound correctly. Harvest once, replay immediately, and assert no second economic credit is created absent real external state change.
