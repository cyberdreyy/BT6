### Title
Divergent duplicate priority/cost computations between forwarding-stage and banking-stage scheduler allow transaction-ordering manipulation - (File: core/src/forwarding_stage.rs, core/src/transaction_priority.rs)

### Summary
The reported ERC4626 bug is a class of "duplicate logic divergence": two independently implemented functions (`deposit`/`convertToShares` vs `mint`/`previewMint`) are meant to compute equivalent economic values, but one path implements a special edge-case rule the other omits, letting an attacker exploit the mismatch. Agave has an analogous pattern in its transaction-prioritization logic used to admit/order transactions from the TPU: two independently written functions compute a transaction's "priority" from the same wire bytes using two different cost models, and only one of the two pairings is covered by a consistency test.

### Finding Description
Agave computes transaction priority `P = R / (1 + C)` in at least two independent places for ordinary, unprivileged, user-submitted transactions:

1. `core/src/forwarding_stage.rs::calculate_priority` — used by the forwarding stage to decide which incoming transactions get forwarded/how they are ordered. It computes cost via the "estimate" cost model: [1](#0-0) 

2. `core/src/transaction_priority.rs::calculate_priority_and_cost` (invoked from `calculate_priority_from_bytes`) — documented as computing "scheduler-side queue priority" and the "sigverify-side floor check." It computes cost via the "executed transaction" cost model, using the transaction's *sanitized* `compute_unit_limit` from `TransactionConfiguration`: [2](#0-1) [3](#0-2) 

These two implementations use different `CostModel` entry points — `CostModel::estimate_cost` in the forwarding path vs. `CostModel::calculate_cost_for_executed_transaction` in the scheduler/sigverify path — and different fee-details construction (`FeeDetails::new` built manually in `forwarding_stage.rs` vs. `solana_fee::calculate_fee_details` in `transaction_priority.rs`). The repository even contains an explicit unit test asserting that "the bytes-path and the typed-path must agree on the same packet, since the scheduler-side queue priority is computed via the typed path and the sigverify-side floor check via the bytes path": [4](#0-3) 

However, that test only proves agreement between `calculate_priority_from_bytes` and `calculate_priority_and_cost` (both in `transaction_priority.rs`) — it does not cover `forwarding_stage::calculate_priority`, which is a third, separately maintained re-implementation of the same "P = R / (1 + C)" formula with a different cost-estimation function. This is structurally identical to the ERC4626 bug: multiple call sites intended to compute the same economic quantity, only some of which are kept in lock-step by tests, leaving the others free to diverge as the cost model or compute-budget defaults evolve (e.g., default CU limit heuristics, migrating-builtin cost accounting in `compute-budget-instruction/src/compute_budget_instruction_details.rs`).

### Impact Explanation
If `CostModel::estimate_cost` (forward-facing, used for admission/forwarding decisions) and `CostModel::calculate_cost_for_executed_transaction` (used for the real scheduler priority and block-cost accounting) diverge for certain transaction shapes (e.g., transactions without an explicit `SetComputeUnitLimit` instruction, or ones exercising migrating builtins whose cost differs based on feature activation), an attacker can craft transactions that:
- Appear cheap/low-cost to the forwarding stage (getting favorably forwarded or bypassing throttling logic based on priority), while
- Being expensive under the scheduler's true accounting once accepted into the banking pipeline, disproportionately consuming a leader's per-block/per-account cost budget relative to the fee actually paid.

This is a TPU-ingest / banking-stage prioritization fairness and potential ingest-starvation issue: an attacker can manipulate relative ordering/admission decisions rather than the fee actually charged (fee charging itself, via `solana_fee::calculate_fee_details`, is centralized in `fee/src/lib.rs` and not affected). The severity depends on how large a divergence between `estimate_cost` and `calculate_cost_for_executed_transaction` can be produced for a given wire transaction, which requires further empirical comparison of the two `CostModel` functions across all instruction shapes.

### Likelihood Explanation
Likelihood is moderate: the divergence is reachable by any user submitting a normally-signed transaction over QUIC/TPU — no privileged action, leaked key, or malicious snapshot is required, satisfying the "ordinary user transaction" reachability bar. However, exploitation depends on finding a transaction shape where `CostModel::estimate_cost` and `CostModel::calculate_cost_for_executed_transaction` numerically diverge enough to matter; this was not verified in this session because the two `CostModel` function implementations were not fully diffed line-by-line and the exact bounds of admissible divergence remain unconfirmed. The consistency test only demonstrates that the two `transaction_priority.rs` functions agree — it does not demonstrate that the third implementation in `forwarding_stage.rs` agrees with either.

### Recommendation
- Replace the independent priority/cost implementation in `core/src/forwarding_stage.rs::calculate_priority` with a call into the shared `core/src/transaction_priority.rs::calculate_priority_and_cost` (or `calculate_priority_from_bytes`) function so there is a single source of truth for the "P = R / (1 + C)" computation and its underlying cost model, mirroring the same pattern already used to converge the sigverify-floor and scheduler paths.
- Add a cross-module unit/integration test asserting `forwarding_stage::calculate_priority` and `transaction_priority::calculate_priority_and_cost` produce identical priorities for the same wire bytes, analogous to the existing `floor_priority_from_bytes_matches_typed_path` test.
- Audit `CostModel::estimate_cost` vs `CostModel::calculate_cost_for_executed_transaction` for behavioral differences on the "no explicit compute-budget instruction" default path and on migrating-builtin instructions, since these are exactly the edge cases (analogous to ERC4626's zero-supply case) most likely to diverge.

### Proof of Concept
A concrete divergence-triggering transaction was not constructed in this session; doing so requires directly comparing `CostModel::estimate_cost(...)` and `CostModel::calculate_cost_for_executed_transaction(...)` output for the same `RuntimeTransaction` across representative instruction sets (no `ComputeBudgetInstruction`, one with only `SetComputeUnitPrice`, one invoking a migrating builtin) to identify a case where the ratio of `reward/(cost+1)` differs meaningfully between `core/src/forwarding_stage.rs::calculate_priority` and `core/src/transaction_priority.rs::calculate_priority_and_cost`. This is flagged as an open verification item rather than a confirmed exploit.

### Citations

**File:** core/src/forwarding_stage.rs (L601-627)
```rust
fn calculate_priority(
    transaction: &RuntimeTransaction<SanitizedTransactionView<&[u8]>>,
    bank: &Bank,
) -> Option<u64> {
    let transaction_configuration = transaction
        .transaction_configuration(&bank.feature_set)
        .ok()?;

    // Manually estimate fee here since currently interface doesn't allow a on SVM type.
    // Doesn't need to be 100% accurate so long as close and consistent.
    let prioritization_fee = transaction_configuration.priority_fee_lamports;
    let signature_details = transaction.signature_details();
    let signature_fee = signature_details
        .total_signatures()
        .saturating_mul(bank.fee_structure().lamports_per_signature);
    let fee_details = FeeDetails::new(signature_fee, prioritization_fee);

    let reward = bank
        .calculate_reward_and_burn_fee_details(&CollectorFeeDetails::from(fee_details))
        .get_deposit();

    let cost = CostModel::estimate_cost(
        transaction,
        transaction.program_instructions_iter(),
        transaction.num_requested_write_locks(),
        &bank.feature_set,
    );
```

**File:** core/src/transaction_priority.rs (L32-66)
```rust
pub(crate) fn calculate_priority_and_cost<Tx: TransactionMeta + SVMStaticMessage>(
    bank: &Bank,
    transaction: &Tx,
    transaction_configuration: &TransactionConfiguration,
) -> (u64, u64) {
    let cost = CostModel::calculate_cost_for_executed_transaction(
        transaction,
        u64::from(transaction_configuration.compute_unit_limit),
        transaction_configuration.loaded_accounts_data_size_limit,
        &bank.feature_set,
    )
    .sum();
    let fee_details = solana_fee::calculate_fee_details(
        transaction,
        bank.fee_structure().lamports_per_signature,
        transaction_configuration.priority_fee_lamports,
        bank.fee_features(),
    );
    let reward = bank
        .calculate_reward_and_burn_fee_details(&CollectorFeeDetails::from(fee_details))
        .get_deposit();

    // We need a multiplier here to avoid rounding down too aggressively.
    // For many transactions, the cost will be greater than the fees in terms of raw lamports.
    // For the purposes of calculating prioritization, we multiply the fees by a large number so that
    // the cost is a small fraction.
    // An offset of 1 is used in the denominator to explicitly avoid division by zero.
    const MULTIPLIER: u64 = 1_000_000;
    (
        reward
            .saturating_mul(MULTIPLIER)
            .saturating_div(cost.saturating_add(1)),
        cost,
    )
}
```

**File:** core/src/transaction_priority.rs (L73-88)
```rust
pub(crate) fn calculate_priority_from_bytes(bank: &Bank, data: &[u8]) -> Option<u64> {
    let view = SanitizedTransactionView::try_new_sanitized(data, &sanitize_config()).ok()?;
    let runtime_tx = RuntimeTransaction::<SanitizedTransactionView<_>>::try_new(
        view,
        MessageHash::Compute,
        None,
    )
    .ok()?;
    let transaction_configuration = runtime_tx
        .transaction_configuration(&bank.feature_set)
        .ok()?;
    let (priority, _cost) =
        calculate_priority_and_cost(bank, &runtime_tx, &transaction_configuration);

    Some(priority)
}
```

**File:** core/src/transaction_priority.rs (L167-192)
```rust
    #[test]
    fn floor_priority_from_bytes_matches_typed_path() {
        // The bytes-path and the typed-path must agree on the same packet,
        // since the scheduler-side queue priority is computed via the typed
        // path and the sigverify-side floor check via the bytes path.
        let (bank, mint) = test_bank();
        let bytes = make_tx_bytes(&mint, bank.last_blockhash(), 100);

        let from_bytes = priority_from(&bank, &bytes);

        let view =
            SanitizedTransactionView::try_new_sanitized(&bytes[..], &sanitize_config()).unwrap();
        let runtime_tx = RuntimeTransaction::<SanitizedTransactionView<_>>::try_new(
            view,
            MessageHash::Compute,
            None,
        )
        .unwrap();
        let transaction_configuration = runtime_tx
            .transaction_configuration(&bank.feature_set)
            .unwrap();
        let (from_typed, _cost) =
            calculate_priority_and_cost(&bank, &runtime_tx, &transaction_configuration);

        assert_eq!(from_bytes, from_typed);
    }
```
