# Q3139: account_locks::is_locked_readonly — clean/shrink race

## Question
Can an unprivileged attacker, through accounts created/mutated by an unprivileged fee-payer transaction, reach `account_locks::is_locked_readonly` and time closes and writes so cleaning removes a live account version or shrink drops committed data, so that the invariant "clean/shrink never removes an account version reachable by a rooted slot" is violated, leading to Consensus / Liveness?

## Target
- File/function: `accounts-db/src/account_locks.rs` -> `is_locked_readonly`
- Entrypoint: accounts created/mutated by an unprivileged fee-payer transaction
- Attacker controls: close/write timing across slots for accounts it owns
- Exploit idea: Time closes and writes so cleaning removes a live account version or shrink drops committed data.
- Invariant to test: clean/shrink never removes an account version reachable by a rooted slot.
- Expected Immunefi impact: Consensus / Liveness — Critical
- Fast validation: write an accounts-db test storing/reloading the crafted account and asserting index==storage and no panic.
