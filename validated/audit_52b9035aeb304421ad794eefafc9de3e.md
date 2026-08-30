### Title
Subsidized-amount ledger entry is not rolled back with the receipt, causing a phantom mint accounting mismatch - (File: runtime/runtime/src/lib.rs)

### Summary
`apply_action_receipt` commits or rolls back `state_update` based on `result.result` (lines 1024-1034), but the subsequent fold of `result.subsidized_amount` into `stats.balance.subsidized_amount` (lines 1089-1090) is performed unconditionally, with no check on whether the receipt actually succeeded. Because `subsidized_amount` is contributed per-action inside `action_function_call` in `runtime/runtime/src/function_call.rs:223-224` whenever that individual action succeeds, a receipt whose *earlier* action creates a 1-yocto subsidized promise to a victim account `V` and whose *later* action fails still leaves the earlier action's `subsidized_amount` contribution present on the aggregated `ActionResult`, even though the overall receipt is rolled back.

### Finding Description
In `apply_action_receipt`:
- The action loop iterates over `action_receipt.actions()`, calling `apply_action` per action and merging its `ActionResult` into the aggregate `result` via `result.merge(new_result)?` at `runtime/runtime/src/lib.rs:927`.
- `action_function_call` only mutates its local `ActionResult` (`result.subsidized_amount`, `result.new_receipts`) when `execution_succeeded` is true for that specific action [1](#0-0) .
- If a subsequent action in the same batch fails, the loop breaks with `result.result` set to `Err` [2](#0-1) , and `state_update.rollback()` is invoked instead of `commit()` [3](#0-2) .
- Despite the rollback, `stats.balance.subsidized_amount` is incremented from `result.subsidized_amount` with no gating on `result.result` [4](#0-3) .

`state_update.rollback()` only discards trie-level state changes; it has no effect on the in-memory `ActionResult` accumulator, so any `subsidized_amount` contributed by an earlier, successful action inside the same failed receipt survives into the chunk-level ledger even though the corresponding subsidized receipt to `V` is discarded along with the rest of the failed batch's side effects.

### Impact Explanation
This produces a supply-conservation ledger mismatch: `stats.balance.subsidized_amount` records tokens as minted/subsidized to `V` that were never actually created or transferred, because the underlying receipt was rolled back. This falls under NEAR's token-inflation/accounting-integrity bounty category (chunk-level balance ledger divergence from actual on-chain state), which can contribute to state-root/witness balance-checking inconsistencies if such ledgers feed into protocol-level conservation checks.

### Likelihood Explanation
The precondition requires an ordinary account to submit a receipt/transaction containing at least two actions in one batch: an early `FunctionCall` that creates a subsidized (1-yocto, `skip_deduct`) promise to a victim account, followed by another action that deterministically fails (e.g., a bad method call, insufficient gas for a later action, or a deliberate `panic!` in a later cross-contract call within the same action list). This requires no special privileges, keys, or validator access — any signer with a deployed wasm contract can trigger it, making it fully reachable by an unprivileged attacker and repeatable at will.

### Recommendation
Gate the `stats.balance.subsidized_amount` accumulation on `result.result.is_ok()`, symmetric with the `state_update.commit()`/`rollback()` branch, e.g. only add `result.subsidized_amount` inside the `Ok(_)` arm of the match at lines 1024-1034, mirroring how `result.new_receipts` (i.e., the subsidized promise) is discarded on rollback.

### Proof of Concept
Add a runtime integration test (e.g. in `runtime/runtime/src/tests/apply.rs`) that:
1. Deploys a contract on account `A` with zero extra balance beyond storage staking.
2. Submits one receipt to `A` with two actions: (a) a `FunctionCall` that issues a subsidized (1-yocto, skip-deduct) `promise_batch_action_transfer`/function-call promise to victim account `V`, and (b) a subsequent action that unconditionally fails (e.g., calling a nonexistent method or a host function guaranteed to abort).
3. Applies the chunk and asserts:
   - `V`'s balance is unchanged (the subsidized receipt never executed).
   - The returned `ApplyStats`/`stats.balance.subsidized_amount` for this receipt is `0`, not `1`.
4. Verify the test fails on current code (`subsidized_amount == 1`) and passes after gating the addition on `result.result.is_ok()`.

### Citations

**File:** runtime/runtime/src/function_call.rs (L151-227)
```rust
    if execution_succeeded {
        // Fetch metadata for PromiseYield timeout queue
        let mut promise_yield_indices = get_promise_yield_indices(state_update)?;
        let initial_promise_yield_indices = promise_yield_indices.clone();

        let mut new_receipts: Vec<_> = receipt_manager
            .action_receipts
            .into_iter()
            .map(|receipt| {
                // If the newly created receipt is a PromiseYield, enqueue a timeout for it
                if receipt.is_promise_yield {
                    enqueue_promise_yield_timeout(
                        state_update,
                        &mut promise_yield_indices,
                        account_id.clone(),
                        receipt.input_data_ids[0],
                        apply_state.block_height
                            + config.wasm_config.limit_config.yield_timeout_length_in_blocks,
                    );
                }

                let new_action_receipt = ActionReceiptV2 {
                    signer_id: action_receipt.signer_id().clone(),
                    signer_public_key: action_receipt.signer_public_key().clone(),
                    refund_to: receipt.refund_to,
                    gas_price: action_receipt.gas_price(),
                    output_data_receivers: receipt.output_data_receivers,
                    input_data_ids: receipt.input_data_ids,
                    actions: receipt.actions,
                };
                let new_receipt = if receipt.is_promise_yield {
                    ReceiptEnum::PromiseYieldV2(new_action_receipt)
                } else {
                    ReceiptEnum::ActionV2(new_action_receipt)
                };

                Receipt::V0(ReceiptV0 {
                    predecessor_id: account_id.clone(),
                    receiver_id: receipt.receiver_id,
                    // Actual receipt ID is set in the Runtime.apply_action_receipt(...) in the
                    // "Generating receipt IDs" section
                    receipt_id: CryptoHash::default(),
                    receipt: new_receipt,
                })
            })
            .collect();

        // Create data receipts for resumed yields
        new_receipts.extend(receipt_manager.data_receipts.into_iter().map(|receipt| {
            let new_data_receipt = DataReceipt { data_id: receipt.data_id, data: receipt.data };

            Receipt::V0(ReceiptV0 {
                predecessor_id: account_id.clone(),
                receiver_id: account_id.clone(),
                // Actual receipt ID is set in the Runtime.apply_action_receipt(...) in the
                // "Generating receipt IDs" section
                receipt_id: CryptoHash::default(),
                receipt: if receipt.is_promise_resume {
                    ReceiptEnum::PromiseResume(new_data_receipt)
                } else {
                    ReceiptEnum::Data(new_data_receipt)
                },
            })
        }));

        // Commit metadata for yielded promises queue
        if promise_yield_indices != initial_promise_yield_indices {
            set_promise_yield_indices(state_update, &promise_yield_indices);
        }

        account.set_amount(outcome.balance);
        account.set_storage_usage(outcome.storage_usage);
        result.subsidized_amount =
            safe_add_balance(result.subsidized_amount, outcome.subsidized_amount)?;
        result.result = Ok(outcome.return_data);
        result.new_receipts.extend(new_receipts);
    }
```

**File:** runtime/runtime/src/lib.rs (L946-951)
```rust
                // TODO storage error
                if let Err(ref mut res) = result.result {
                    res.index = Some(action_index as u64);
                    break;
                }
            }
```

**File:** runtime/runtime/src/lib.rs (L1024-1034)
```rust
        // Committing or rolling back state.
        match &result.result {
            Ok(_) => {
                state_update.commit(StateChangeCause::ReceiptProcessing {
                    receipt_hash: receipt.get_hash(),
                });
            }
            Err(_) => {
                state_update.rollback();
            }
        };
```

**File:** runtime/runtime/src/lib.rs (L1087-1090)
```rust
        stats.balance.tx_burnt_amount =
            safe_add_balance(stats.balance.tx_burnt_amount, tx_burnt_amount)?;
        stats.balance.subsidized_amount =
            safe_add_balance(stats.balance.subsidized_amount, result.subsidized_amount)?;
```
