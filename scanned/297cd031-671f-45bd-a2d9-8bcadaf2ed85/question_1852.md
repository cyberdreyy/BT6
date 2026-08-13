# Q1852: kamino_harvest_reward: reward ownership is bound to the wrong integration position [duplicate-metas-that-alter-which] [source-destination]

## Question
Can an unprivileged attacker invoke `kamino_harvest_reward` with duplicate metas that alter which obligation is interpreted as source so `kamino_harvest_reward` harvests rewards from the wrong integration position/owner context, violating `Kamino reward harvest must claim exactly once from the correct obligation and deliver only to the canonical destination` and causing `High: direct reward theft or repeated reward credit`? Focus specifically on binding the reward source position and final destination together under the same owner context.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/harvest_reward.rs` / `kamino_harvest_reward`
- Entrypoint: `kamino_harvest_reward`
- Attacker controls: duplicate metas that alter which obligation is interpreted as source
- Exploit idea: Try cross-user/cross-position substitutions for external reward accounts that superficially satisfy type checks. Focus specifically on binding the reward source position and final destination together under the same owner context.
- Invariant to test: Kamino reward harvest must claim exactly once from the correct obligation and deliver only to the canonical destination
- Expected Immunefi impact: High: direct reward theft or repeated reward credit
- Fast validation: Use multiple funded positions and assert harvest cannot pull from one while crediting another. Mix source positions and destinations across users and assert harvest can neither pull from one nor pay another incorrectly.
