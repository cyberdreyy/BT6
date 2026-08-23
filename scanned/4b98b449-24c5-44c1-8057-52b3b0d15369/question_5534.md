# Q5534: sdk_transactions::from_transaction_for_tests — compute-budget parse mismatch

## Question
Can an unprivileged attacker, through a transaction submitted by an unprivileged fee-payer, reach `sdk_transactions::from_transaction_for_tests` and craft compute-budget instructions so the parsed CU/price/heap limits differ from what execution enforces, so that the invariant "parsed compute-budget limits equal the limits enforced during execution" is violated, leading to Consensus (metering divergence)?

## Target
- File/function: `runtime-transaction/src/runtime_transaction/sdk_transactions.rs` -> `from_transaction_for_tests`
- Entrypoint: a transaction submitted by an unprivileged fee-payer
- Attacker controls: the ComputeBudgetInstruction variants and values it includes
- Exploit idea: Craft compute-budget instructions so the parsed CU/price/heap limits differ from what execution enforces.
- Invariant to test: parsed compute-budget limits equal the limits enforced during execution.
- Expected Immunefi impact: Consensus (metering divergence) — Critical
- Fast validation: write a unit/fuzz test decoding the crafted message and assert sanitized privileges == enforced privileges.
