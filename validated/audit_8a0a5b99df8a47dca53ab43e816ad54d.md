No vulnerability found for this question.

`Receipt::balance_refund_receiver()` deliberately returns whatever `refund_to` a receipt's creator (its `predecessor_id`) set via the documented `promise_set_refund_to` host function, falling back to `predecessor_id` only if unset [1](#0-0) . This same mechanism is exercised identically for `action_deterministic_state_init`'s balance refund at [2](#0-1) , so no special bug exists in that function — it just reuses the generic, already-existing refund-redirection primitive.

This behavior is intentional and documented: "If a receipt fails execution, a balance refund usually goes to the predecessor of the receipt. However, the predecessor can set this to another account id when sending the receipt" [3](#0-2) . The host function `promise_set_refund_to` is implemented and tested precisely for this purpose [4](#0-3) , and an existing runtime test `test_refund_to` confirms the exact scenario the question describes (an intermediary contract, `near_1`, forwards a caller's deposit to a promise targeting `near_2` and redirects the refund to a third account, `near_3`, rather than back to itself or the original depositor) is expected, working, and asserted behavior of the system [5](#0-4) .

Critically, the deposit attached to the new receipt is deducted from the balance of the receipt's creator (the intermediary contract) at the moment the promise/receipt is dispatched — not directly from any "original depositing caller" several hops away. That intermediary is therefore the entity that actually funded `action.deposit` on the new receipt, and it is the same entity entitled to choose (or delegate) where any refund goes via `refund_to`. An untrusted intermediary contract mishandling a caller's funds is a standard smart-contract trust/business-logic risk chosen by the caller when they invoke that contract — it is not a runtime/protocol bug, and it is not unique to `DeterministicStateInitAction`; the same redirection applies uniformly to `FunctionCall`, `Transfer`, and other receipt-refund paths.

### Citations

**File:** runtime/near-vm-runner/src/logic/logic.rs (L809-814)
```rust
    /// Populates a register with the ID of an account which would receive a refund.
    ///
    /// This is the ID of an account set for the current receipt by its
    /// predecessor via [`Self::promise_set_refund_to()`], or
    /// [`Self::predecessor_account_id()`] otherwise.
    ///
```

**File:** runtime/near-vm-runner/src/logic/logic.rs (L2497-2535)
```rust
    /// Sets the `refund_to` field on the promise
    ///
    /// # Errors
    ///
    /// * If `promise_idx` does not correspond to an existing promise returns `InvalidPromiseIndex`;
    /// * If `account_id_len + account_id_ptr` points outside the memory of the guest or host
    /// returns `MemoryAccessViolation`.
    /// * If called as view function returns `ProhibitedInView`.
    ///
    /// # Cost
    ///
    /// `base + cost of reading and decoding the account id`
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
    }
```

**File:** runtime/runtime/src/deterministic_account_id.rs (L87-91)
```rust
    if deposit_refund > Balance::ZERO {
        result
            .new_receipts
            .push(Receipt::new_balance_refund(receipt.balance_refund_receiver(), deposit_refund));
    }
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

**File:** runtime/runtime/tests/test_async_calls.rs (L1204-1296)
```rust
// redirect the balance refund using `promise_refund_to`
#[test]
fn test_refund_to() {
    let group = RuntimeGroup::new(4, 4, near_test_contracts::rs_contract());

    let signer_sender = group.signers[0].clone();
    let signer_receiver = group.signers[1].clone();
    let deposit = Balance::from_yoctonear(1000);

    let data = serde_json::json!([
        {
            "batch_create": {
                "account_id": "near_2",
            },
            "id": 0
        },
        {
            "action_function_call": {
                "promise_index": 0,
                "method_name": "non_existing_function",
                "arguments": [],
                "amount": deposit,
                "gas": GAS_2,
            },
            "id": 0
        },
        {
            "set_refund_to": {
                "promise_index": 0,
                "beneficiary_id": "near_3"
            }, "id": 0
        }
    ]);

    let signed_transaction = SignedTransaction::from_actions(
        1,
        signer_sender.get_account_id(),
        signer_receiver.get_account_id(),
        &signer_sender,
        vec![Action::FunctionCall(Box::new(FunctionCallAction {
            method_name: "call_promise".to_string(),
            args: serde_json::to_vec(&data).unwrap(),
            gas: GAS_1,
            deposit,
        }))],
        CryptoHash::default(),
    );

    let handles = RuntimeGroup::start_runtimes(group.clone(), vec![signed_transaction.clone()]);
    for h in handles {
        h.join().unwrap();
    }

    println!("{:?}", group.executed_receipts);

    use near_primitives::transaction::*;
    let [r0] = &*assert_receipts!(group, signed_transaction) else {
        panic!("Incorrect number of produced receipts")
    };

    let receipts = &*assert_receipts!(group, "near_0" => r0 @ "near_1",
        ReceiptEnum::Action(ActionReceipt{actions, ..}) | ReceiptEnum::ActionV2(ActionReceiptV2{actions, ..}),
        {},
        actions,
        a0, Action::FunctionCall(function_call_action), {
            assert_eq!(function_call_action.gas, GAS_1);
            assert_eq!(function_call_action.deposit, deposit);
            assert_eq!(function_call_action.method_name, "call_promise");
        }
    );
    let [r1, refunds @ ..] = &receipts else { panic!("Incorrect number of produced receipts") };
    group.assert_gas_refunds(&refunds[..]);

    let receipts = &*assert_receipts!(group, "near_1" => r1 @ "near_2",
        ReceiptEnum::Action(ActionReceipt{actions, ..}) | ReceiptEnum::ActionV2(ActionReceiptV2{actions, ..}),
        {},
        actions,
        a0, Action::FunctionCall(function_call_action), {
            assert_eq!(function_call_action.gas, GAS_2);
            assert_eq!(function_call_action.deposit, deposit);
            assert_eq!(function_call_action.method_name, "non_existing_function");
        }
    );
    // The redirected deposit refund (to `near_3`) is emitted first; any trailing receipt is the
    // gas refund for executing this receipt.
    let [deposit_refund, refunds @ ..] = &receipts else {
        panic!("Incorrect number of produced receipts")
    };
    group.assert_gas_refunds(&refunds[..]);

    // This is the redirected refund
    assert_refund!(group, deposit_refund @ "near_3");
}
```
