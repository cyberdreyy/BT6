# Q1270: runtime_transaction::is_signer — duplicate-account sanitization

## Question
Can an unprivileged attacker, through a transaction submitted by an unprivileged fee-payer, reach `runtime_transaction::is_signer` and include duplicate or overlapping account keys that pass sanitization but confuse privilege accounting downstream, so that the invariant "sanitized transactions have a consistent single privilege per account key" is violated, leading to Loss of Funds / Consensus?

## Target
- File/function: `runtime-transaction/src/runtime_transaction.rs` -> `is_signer`
- Entrypoint: a transaction submitted by an unprivileged fee-payer
- Attacker controls: the account key list and header counts in its message
- Exploit idea: Include duplicate or overlapping account keys that pass sanitization but confuse privilege accounting downstream.
- Invariant to test: sanitized transactions have a consistent single privilege per account key.
- Expected Immunefi impact: Loss of Funds / Consensus — Critical
- Fast validation: write a unit/fuzz test decoding the crafted message and assert sanitized privileges == enforced privileges.
