# Q3157: account_locks::has_duplicates — read-cache poisoning

## Question
Can an unprivileged attacker, through accounts created/mutated by an unprivileged fee-payer transaction, reach `account_locks::has_duplicates` and populate the read-only accounts cache so a stale account is served to execution after an update, so that the invariant "the read-only cache never serves a version older than committed state" is violated, leading to Consensus/Safety Violation?

## Target
- File/function: `accounts-db/src/account_locks.rs` -> `has_duplicates`
- Entrypoint: accounts created/mutated by an unprivileged fee-payer transaction
- Attacker controls: access patterns to an account it just wrote
- Exploit idea: Populate the read-only accounts cache so a stale account is served to execution after an update.
- Invariant to test: the read-only cache never serves a version older than committed state.
- Expected Immunefi impact: Consensus/Safety Violation — Critical
- Fast validation: write an accounts-db test storing/reloading the crafted account and asserting index==storage and no panic.
