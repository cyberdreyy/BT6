No vulnerability confirmed. The premise of the question does not match the code.

When a `PromiseResume` arrives, the runtime does **not** call `apply_action_receipt` with the resume receipt itself. Instead, it fetches the originally-stored `PromiseYield` receipt from state via `get_promise_yield_receipt(state_update, account_id, data_receipt.data_id)` and passes that stored `yield_receipt` into `apply_action_receipt`: [1](#0-0) 

Consequently, at `actor_id = receipt.predecessor_id().clone()` in `apply_action_receipt`, `receipt` is the original `yield_receipt`, so `actor_id` is seeded from the **original** yield-creating account's predecessor — not from whoever sent the `PromiseResume`: [2](#0-1) 

That `predecessor_id` was fixed at yield-creation time in `function_call.rs`, where new receipts (including `PromiseYield`) are stamped with `predecessor_id: account_id.clone()` — the account that executed `promise_yield_create`/`promise_yield_create_with_id`, not the later resumer: [3](#0-2) 

The `PromiseResume` receipt (whose `predecessor_id` reflects the resuming caller) is only used to locate and deliver the `ReceivedData`/timeout; it is never substituted for the stored yield receipt when computing `actor_id`: [4](#0-3) 

So `check_actor_permissions` (used for `AddKey`/`Stake` gating in `apply_action`) is anchored to the correct original creator regardless of which account triggers the resume, contradicting the premise that the resuming account's identity leaks into `actor_id`. [5](#0-4) 

#No vulnerability found for this question.

### Citations

**File:** runtime/runtime/src/lib.rs (L563-567)
```rust
        // Permission validation
        if let Err(e) = check_actor_permissions(action, account, actor_id, account_id) {
            result.result = Err(e);
            return Ok(result);
        }
```

**File:** runtime/runtime/src/lib.rs (L855-855)
```rust
        let mut actor_id = receipt.predecessor_id().clone();
```

**File:** runtime/runtime/src/lib.rs (L1500-1562)
```rust
            VersionedReceiptEnum::PromiseResume(data_receipt) => {
                if data_receipt.data.is_none() {
                    // This is a timeout resume. Check the status to see if the receipt has been resumed.
                    let status =
                        get_promise_yield_status(state_update, account_id, data_receipt.data_id)?;
                    if status == Some(PromiseYieldStatus::ResumeInitiated) {
                        // A non-timeout resume receipt has been sent, cancel the timeout.
                        return Ok(None);
                    }
                }

                // Received a new PromiseResume receipt delivering input data for a PromiseYield.
                // It is guaranteed that the PromiseYield has exactly one input data dependency
                // and that it arrives first, so we can simply find and execute it.
                if let Some(yield_receipt) =
                    get_promise_yield_receipt(state_update, account_id, data_receipt.data_id)?
                {
                    // Remove the receipt from the state
                    remove_promise_yield_receipt(state_update, account_id, data_receipt.data_id);

                    // Clear the PromiseYield status
                    remove_promise_yield_status(state_update, account_id, data_receipt.data_id);

                    // Clean up yield_id <-> data_id mappings if this was created by yield_create_with_id
                    if ProtocolFeature::YieldWithId.enabled(apply_state.current_protocol_version) {
                        if let Some(yield_id) = get_yield_id_for_data_id(
                            state_update,
                            account_id,
                            data_receipt.data_id,
                        )? {
                            remove_yield_id_mappings(
                                state_update,
                                account_id,
                                yield_id,
                                data_receipt.data_id,
                            );
                        }
                    }

                    // Save the data into the state keyed by the data_id
                    set_received_data(
                        state_update,
                        account_id.clone(),
                        data_receipt.data_id,
                        &ReceivedData { data: data_receipt.data.clone() },
                    );

                    // Execute the PromiseYield receipt. It will read the input data and clean it
                    // up from the state.
                    return self
                        .apply_action_receipt(
                            state_update,
                            apply_state,
                            pipeline_manager,
                            &yield_receipt,
                            receipt_sink,
                            instant_receipts,
                            validator_proposals,
                            stats,
                            epoch_info_provider,
                            receipt_to_tx,
                        )
                        .map(Some);
```

**File:** runtime/runtime/src/function_call.rs (L181-194)
```rust
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
```
