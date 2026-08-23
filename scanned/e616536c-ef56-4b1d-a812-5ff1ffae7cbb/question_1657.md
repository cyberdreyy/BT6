# Q1657: nonce_info::create_nonce_account — rollback misapplication

## Question
Can an unprivileged attacker, through a transaction executed through the SVM/Bank by an unprivileged fee-payer, reach `nonce_info::create_nonce_account` and force a transaction failure so RollbackAccounts restores stale lamports/data, keeping funds it should have paid, so that the invariant "on failure, only fee/nonce state persists and all else rolls back exactly" is violated, leading to Loss of Funds?

## Target
- File/function: `svm/src/nonce_info.rs` -> `create_nonce_account`
- Entrypoint: a transaction executed through the SVM/Bank by an unprivileged fee-payer
- Attacker controls: a transaction crafted to fail after partial account changes
- Exploit idea: Force a transaction failure so RollbackAccounts restores stale lamports/data, keeping funds it should have paid.
- Invariant to test: on failure, only fee/nonce state persists and all else rolls back exactly.
- Expected Immunefi impact: Loss of Funds — Critical
- Fast validation: write an SVM/bank test running the transaction twice and asserting deterministic, exact fee/rollback/rent accounting.
