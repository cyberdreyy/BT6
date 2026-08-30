### No Vulnerability found for this question.

Analysis: `promise_set_refund_to` is a standard host function that lets the **predecessor of the currently-executing receipt** override the destination of the refund for a promise (sub-receipt) that predecessor itself creates [1](#0-0) . By design, refunds normally go to the predecessor of the receipt, and the predecessor may redirect them elsewhere [2](#0-1) .

In the `DelegateAction`/meta-transaction flow, `apply_delegate_action` creates the inner receipt with `predecessor_id: sender_id` [3](#0-2) . The code contains an explicit comment acknowledging exactly this scenario: the relayer prepays the attached deposit, the deposit refund on failure normally goes to `sender_id` (the receipt's predecessor from the relayer's point of view), contracts commonly forward/keep/redirect that deposit, and therefore **"Relayer should verify DelegateAction before submitting it because it spends the attached deposit"** [4](#0-3) .

This means `sender_id`'s contract already has full, intended authority over the relayer-funded deposit once the inner receipt executes at `sender_id` — it can spend it, forward it to a sub-promise, keep it, or (via `promise_set_refund_to`) redirect that sub-promise's refund. None of these actions constitute privilege escalation beyond what the contract already has as the receiver/predecessor of that receipt; the relayer's exposure to this outcome is a known, documented tradeoff of meta-transactions, not an unauthorized override of a security boundary. There is no additional signer/relayer-level authorization check that this bypasses, since the relayer never had exclusive control over refund routing for actions it delegates to `sender_id`.

### Citations

**File:** runtime/near-vm-runner/src/logic/logic.rs (L2509-2534)
```rust
    pub fn promise_set_refund_to(
        &mut self,
        promise_idx: u64,
        account_id_len: u64,
        account_id_ptr: u64,
    ) -> Result<()> {
        self.result_state.gas_counter.pay_base(base)?;
        if self.context.is_view() {
            return Err(HostError::ProhibitedInView {
                method_name: "promise_set_refund_to".to_string(),
            }
            .into());
        }
        let refund_to = self.read_and_parse_account_id(account_id_ptr, account_id_len)?;
        let promise = self
            .promises
            .get(promise_idx as usize)
            .ok_or(HostError::InvalidPromiseIndex { promise_idx })?;

        let receipt_idx = match &promise {
            Promise::Receipt(receipt_idx) => Ok(*receipt_idx),
            Promise::NotReceipt(_) => Err(HostError::CannotSetRefundToOnJointPromise),
        }?;

        self.ext.set_refund_to(receipt_idx, refund_to);
        Ok(())
```

**File:** docs/RuntimeSpec/Components/BindingsSpec/ContextAPI.md (L103-123)
```markdown
#### refund_to_account_id

```rust
refund_to_account_id(register_id: u64)
```

If a receipt fails execution, a balance refund usually goes to the predecessor of the receipt. However, the predecessor
can set this to another account id when sending the receipt.

###### Normal operation

- Saves the bytes of the account id receiving balance refunds into the register.

###### Panics

- If the registers exceed the memory limit panics with `MemoryAccessViolation`;
- If called in a view function panics with `ProhibitedInView`.

###### Current bugs

- Not implemented.
```

**File:** runtime/runtime/src/actions.rs (L483-497)
```rust
    // Generate a new receipt from DelegateAction.
    let new_receipt = Receipt::V0(ReceiptV0 {
        predecessor_id: sender_id.clone(),
        receiver_id: delegate_action.receiver_id().clone(),
        receipt_id: CryptoHash::default(),

        receipt: ReceiptEnum::Action(ActionReceipt {
            signer_id: action_receipt.signer_id().clone(),
            signer_public_key: action_receipt.signer_public_key().clone(),
            gas_price: action_receipt.gas_price(),
            output_data_receivers: vec![],
            input_data_ids: vec![],
            actions: delegate_action.get_actions(),
        }),
    });
```

**File:** runtime/runtime/src/actions.rs (L499-503)
```rust
    // Note, Relayer prepaid all fees and all things required by actions: attached deposits and attached gas.
    // If something goes wrong, deposit is refunded to the predecessor, this is sender_id/Sender in DelegateAction.
    // Gas is refunded to the signer, this is Relayer.
    // Some contracts refund the deposit. Usually they refund the deposit to the predecessor and this is sender_id/Sender from DelegateAction.
    // Therefore Relayer should verify DelegateAction before submitting it because it spends the attached deposit.
```
