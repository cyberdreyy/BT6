# Q3482: accounts_index::run_test_purge_exact_secondary_index — unbounded index growth

## Question
Can an unprivileged attacker, through accounts created/mutated by an unprivileged fee-payer transaction, reach `accounts_index::run_test_purge_exact_secondary_index` and create many accounts/entries so in_mem_accounts_index or bucket_map grows memory without bound, so that the invariant "account-index memory is bounded relative to committed account count" is violated, leading to Liveness / DoS?

## Target
- File/function: `accounts-db/src/accounts_index.rs` -> `run_test_purge_exact_secondary_index`
- Entrypoint: accounts created/mutated by an unprivileged fee-payer transaction
- Attacker controls: the number and key distribution of accounts it creates
- Exploit idea: Create many accounts/entries so in_mem_accounts_index or bucket_map grows memory without bound.
- Invariant to test: account-index memory is bounded relative to committed account count.
- Expected Immunefi impact: Liveness / DoS — High
- Fast validation: write an accounts-db test storing/reloading the crafted account and asserting index==storage and no panic.
