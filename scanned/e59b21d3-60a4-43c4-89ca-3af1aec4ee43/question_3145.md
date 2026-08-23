# Q3145: account_locks::can_write_lock — account-lock exhaustion

## Question
Can an unprivileged attacker, through accounts created/mutated by an unprivileged fee-payer transaction, reach `account_locks::can_write_lock` and request account locks in a pattern that exhausts or corrupts account_locks accounting, so that the invariant "account lock accounting is bounded and released exactly on completion" is violated, leading to DoS (replay stall)?

## Target
- File/function: `accounts-db/src/account_locks.rs` -> `can_write_lock`
- Entrypoint: accounts created/mutated by an unprivileged fee-payer transaction
- Attacker controls: the account sets across transactions it batches
- Exploit idea: Request account locks in a pattern that exhausts or corrupts account_locks accounting.
- Invariant to test: account lock accounting is bounded and released exactly on completion.
- Expected Immunefi impact: DoS (replay stall) — High
- Fast validation: write an accounts-db test storing/reloading the crafted account and asserting index==storage and no panic.
