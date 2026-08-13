# Q1841: kamino_harvest_reward: reward ownership is bound to the wrong integration position [two-obligations-with-type-compatible] [double-claim]

## Question
Can an unprivileged attacker invoke `kamino_harvest_reward` with two obligations with type-compatible reward contexts so `kamino_harvest_reward` harvests rewards from the wrong integration position/owner context, violating `Kamino reward harvest must claim exactly once from the correct obligation and deliver only to the canonical destination` and causing `High: direct reward theft or repeated reward credit`? Focus specifically on one-time reward claim semantics across repeated harvest attempts.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/harvest_reward.rs` / `kamino_harvest_reward`
- Entrypoint: `kamino_harvest_reward`
- Attacker controls: two obligations with type-compatible reward contexts
- Exploit idea: Try cross-user/cross-position substitutions for external reward accounts that superficially satisfy type checks. Focus specifically on one-time reward claim semantics across repeated harvest attempts.
- Invariant to test: Kamino reward harvest must claim exactly once from the correct obligation and deliver only to the canonical destination
- Expected Immunefi impact: High: direct reward theft or repeated reward credit
- Fast validation: Use multiple funded positions and assert harvest cannot pull from one while crediting another. Harvest once, replay immediately, and assert no second economic credit is created absent real external state change.
