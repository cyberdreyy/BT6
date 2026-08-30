#No Vulnerability found for this question.

**Reasoning:**

1. `is_instant_receipt` (`core/primitives/src/receipt.rs:473-491`) only affects how a *newly created* receipt — one produced as an output of the actions currently being executed — is queued: instant receipts go to an in-memory `instant_receipts` deque and are processed immediately after the receipt that spawned them, while non-instant new receipts go out through `ReceiptSink` [1](#0-0) . It does not reorder or preempt receipts that were already dispatched earlier (local/delayed/incoming queues), because it's only consulted at the point a receipt is first created, and processing of already-queued local/delayed/incoming receipts happens in its own separate loop (`process_local_receipts`, `process_delayed_receipts`, `process_incoming_receipts`) unaffected by later instant-receipt classification [2](#0-1) .

2. A "self-initiated DeleteAccount" submitted directly as its own transaction becomes a **local receipt** (signer_id == receiver_id) and is processed by `process_local_receipts` directly — it never passes through the `is_instant_receipt` check at all, since that check is only applied to receipts freshly generated during another receipt's action execution [3](#0-2) . So the described attack precondition (attacker directly deletes their own account) does not even engage the "instant receipt" special-case path being scrutinized.

3. The gas-refund receipt is created via `Receipt::new_gas_refund`, whose `receiver_id` is fixed to the original FunctionCall's signer at the moment of refund creation — it is entirely independent of, and cannot be altered by, a subsequently executed `DeleteAccountAction`'s `beneficiary_id` [4](#0-3) . The `beneficiary_id` only controls the destination of a separate `Receipt::new_balance_refund` created at delete-time for the account's *current* balance [5](#0-4) . There is no code path connecting these two receipts' destinations.

4. If the pending gas-refund receipt (a `Transfer` action from `"system"`) arrives after the account has been deleted, `check_account_existence` requires the account to exist for `Transfer` unless it's eligible for implicit account creation, and the code explicitly states refunds are never eligible for implicit account creation ("Refunds don't automatically create accounts... we don't want some type of abuse") [6](#0-5) . Consequently the refund receipt would fail with `AccountDoesNotExist`, and per the documented refund-failure behavior the deposit is burnt rather than credited to any beneficiary — not misdirected to the attacker.

Given these facts, the proposed exploit chain is not supported by the code: the instant-receipt bypass doesn't reorder independently-dispatched receipts, and the refund destination is never influenced by the attacker-chosen `beneficiary_id`.

### Citations

**File:** runtime/runtime/src/lib.rs (L1139-1180)
```rust
        // Generating receipt IDs
        let receipt_ids = result
            .new_receipts
            .into_iter()
            .enumerate()
            .filter_map(|(receipt_index, mut new_receipt)| {
                let receipt_id = apply_state.create_receipt_id(receipt.receipt_id(), receipt_index);
                new_receipt.set_receipt_id(receipt_id);
                if apply_state.save_receipt_to_tx {
                    receipt_to_tx.push((
                        receipt_id,
                        ReceiptToTxInfo::V1(ReceiptToTxInfoV1 {
                            origin: ReceiptOrigin::FromReceipt(ReceiptOriginReceipt {
                                parent_receipt_id: *receipt.receipt_id(),
                                parent_predecessor_id: receipt.predecessor_id().clone(),
                            }),
                            receiver_account_id: new_receipt.receiver_id().clone(),
                            shard_id: apply_state.shard_id,
                        }),
                    ));
                }
                let is_action = matches!(
                    new_receipt.receipt(),
                    ReceiptEnum::Action(_)
                        | ReceiptEnum::PromiseYield(_)
                        | ReceiptEnum::ActionV2(_)
                        | ReceiptEnum::PromiseYieldV2(_)
                );

                if new_receipt.is_instant_receipt() {
                    // Instant receipts are not sent as outgoing receipts, they will be processed immediately.
                    instant_receipts.push_back(new_receipt);
                } else {
                    // Send out the receipt as an outgoing receipt.
                    if let Err(e) = receipt_sink.forward_or_buffer_receipt(
                        new_receipt,
                        apply_state,
                        state_update,
                    ) {
                        return Some(Err(e));
                    }
                }
```

**File:** runtime/runtime/src/lib.rs (L2641-2670)
```rust
    /// Process a receipt and then immediately process all newly generated instant receipts.
    fn process_receipt_and_instant_receipts(
        &self,
        receipt: &Receipt,
        processing_state: &mut ApplyProcessingReceiptState,
        receipt_sink: &mut ReceiptSink,
        validator_proposals: &mut Vec<ValidatorStake>,
    ) -> Result<(), RuntimeError> {
        self.process_receipt_with_metrics(
            receipt,
            processing_state,
            receipt_sink,
            validator_proposals,
        )?;

        while let Some(instant_receipt) = processing_state.instant_receipts.pop_front() {
            self.process_receipt_with_metrics(
                &instant_receipt,
                processing_state,
                receipt_sink,
                validator_proposals,
            )?;
            processing_state.processed_receipts.push(ProcessedReceipt {
                receipt: instant_receipt,
                source: ReceiptSource::Instant,
            });
        }

        Ok(())
    }
```

**File:** core/primitives/src/receipt.rs (L518-536)
```rust
    pub fn new_gas_refund(
        receiver_id: &AccountId,
        refund: Balance,
        signer_public_key: PublicKey,
    ) -> Self {
        Receipt::V0(ReceiptV0 {
            predecessor_id: "system".parse().unwrap(),
            receiver_id: receiver_id.clone(),
            receipt_id: CryptoHash::default(),
            receipt: ReceiptEnum::Action(ActionReceipt {
                signer_id: receiver_id.clone(),
                signer_public_key,
                gas_price: Balance::ZERO,
                output_data_receivers: vec![],
                input_data_ids: vec![],
                actions: vec![Action::Transfer(TransferAction { deposit: refund })],
            }),
        })
    }
```

**File:** runtime/runtime/src/actions.rs (L364-370)
```rust
    // We use current amount as a pay out to beneficiary.
    let account_balance = account_ref.amount();
    if account_balance > Balance::ZERO {
        result
            .new_receipts
            .push(Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance));
    }
```

**File:** runtime/runtime/src/actions.rs (L857-870)
```rust
fn check_transfer_to_nonexisting_account(
    config: &RuntimeConfig,
    account_id: &AccountId,
    implicit_account_creation_eligible: bool,
) -> Result<(), ActionError> {
    if implicit_account_creation_eligible
        && account_is_implicit(account_id, config.wasm_config.eth_implicit_accounts)
    {
        // OK. It's implicit account creation.
        // Notes:
        // - Transfer action has to be the only action in the transaction to avoid
        // abuse by hijacking this account with other public keys or contracts.
        // - Refunds don't automatically create accounts, because refunds are free and
        // we don't want some type of abuse.
```
