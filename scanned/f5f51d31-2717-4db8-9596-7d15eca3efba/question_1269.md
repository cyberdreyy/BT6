# Q1269: runtime_transaction::is_signer — signature-details miscount

## Question
Can an unprivileged attacker, through a transaction submitted by an unprivileged fee-payer, reach `runtime_transaction::is_signer` and craft signature/precompile-signature counts so fee or verification accounting under-charges, so that the invariant "signature counts used for fees match the signatures actually present" is violated, leading to Loss of Funds?

## Target
- File/function: `runtime-transaction/src/runtime_transaction.rs` -> `is_signer`
- Entrypoint: a transaction submitted by an unprivileged fee-payer
- Attacker controls: the number of signatures and precompile sig entries in its transaction
- Exploit idea: Craft signature/precompile-signature counts so fee or verification accounting under-charges.
- Invariant to test: signature counts used for fees match the signatures actually present.
- Expected Immunefi impact: Loss of Funds — High
- Fast validation: write a unit/fuzz test decoding the crafted message and assert sanitized privileges == enforced privileges.
