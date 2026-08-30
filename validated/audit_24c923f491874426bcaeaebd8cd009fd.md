#No Vulnerability found for this question.

**Rationale:** `ActionResult::merge` at [1](#0-0)  only aggregates gas counters, logs, `new_receipts`, and shifts a `ReturnData::ReceiptIndex` offset so that a `.then()` callback resolves to the correct index in the combined receipt list. It never reads or rewrites any receipt's `receiver_id`. The `receiver_id` of every new receipt is determined earlier, when the executing contract itself calls a promise host function (e.g. `promise_batch_create`, `promise_create`) with an explicit `account_id` argument, and is baked into the receipt by `receipt_manager`/`function_call.rs` before `merge` ever runs, as seen in [2](#0-1) .

Consequently, "substituting a receiver_id into the promise chain via merge" is not mechanically possible: an attacker-controlled callback contract already fully owns the promise it creates (it decided the `receiver_id` itself at creation time via its own wasm code), and `merge` cannot alter that destination — it only concatenates receipt lists and offsets an index. The scenario described (contractA.forward(to=victim2) but contractA sends the transfer to itself instead) is simply the callee contract doing what its own code says, which is the expected and documented trust model for cross-contract calls: whoever receives a deposit/call decides what to do with the funds already delivered to it. No un-consenting third party's funds are diverted by any defect in `merge` or the receipt-index-shifting logic — the "victim" in the given scenario is the one who chose to call an untrusted, attacker-controlled contract with an attached deposit, which is a known and accepted footgun of the permissionless contract-call model, not a protocol bug.

### Citations

**File:** runtime/runtime/src/lib.rs (L439-480)
```rust
    pub fn merge(&mut self, mut next_result: ActionResult) -> Result<(), RuntimeError> {
        assert!(next_result.gas_burnt_for_function_call <= next_result.gas_burnt);
        assert!(
            next_result.gas_burnt <= next_result.gas_used,
            "Gas burnt {} <= Gas used {}",
            next_result.gas_burnt,
            next_result.gas_used
        );
        self.gas_burnt = self.gas_burnt.checked_add_result(next_result.gas_burnt)?;
        self.gas_burnt_for_function_call = self
            .gas_burnt_for_function_call
            .checked_add(next_result.gas_burnt_for_function_call)
            .ok_or(IntegerOverflowError)?;
        self.gas_used = self.gas_used.checked_add_result(next_result.gas_used)?;
        self.compute_usage = safe_add_compute(self.compute_usage, next_result.compute_usage)?;
        // Profile aggregates by summing; each per-action `ActionResult`
        // contributes exactly one entry to the receipt-level contract list.
        self.profile.merge(&next_result.profile);
        self.current_contracts.push(next_result.current_contract);
        self.logs.append(&mut next_result.logs);
        match next_result.result {
            Ok(mut ret_data) => {
                if let ReturnData::ReceiptIndex(ref mut receipt_index) = ret_data {
                    // Shifting local receipt index to be global receipt index.
                    *receipt_index += self.new_receipts.len() as u64;
                }
                self.result = Ok(ret_data);
                self.new_receipts.append(&mut next_result.new_receipts);
                self.validator_proposals.append(&mut next_result.validator_proposals);
                self.tokens_burnt = self
                    .tokens_burnt
                    .checked_add(next_result.tokens_burnt)
                    .ok_or(IntegerOverflowError)?;
                self.subsidized_amount = self
                    .subsidized_amount
                    .checked_add(next_result.subsidized_amount)
                    .ok_or(IntegerOverflowError)?;
            }
            Err(err) => self.set_error(err),
        }
        Ok(())
    }
```

**File:** runtime/runtime/src/function_call.rs (L156-196)
```rust
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
```
