#No Vulnerability found for this question.

**Rationale:** `receipt_index` returned by `create_action_receipt`/`create_promise_yield_receipt` is derived from `self.action_receipts.len()` before the push, so it is strictly monotonically increasing across all `promise_batch_create` calls within a single `ReceiptManager` instance (which is scoped to a single execution) — it is never reused or reset. [1](#0-0) 
Likewise, `action_index` returned from `append_action` is `actions.len() - 1` after pushing onto that specific receipt's action vector, so repeated calls to `append_action_function_call_weight` on the same `receipt_index` always yield a new, incrementing `action_index`. [2](#0-1) [3](#0-2) 

Consequently, every `(receipt_index, action_index)` pair pushed into `gas_weights` is guaranteed unique by construction — there is no code path (reachable from an unprivileged attacker's transaction, contract, or meta-transaction) that causes index reuse or collision. The scenario in the question ("receipt_index reuse across separate promise_batch_create calls") is not supported by the actual index-generation logic, so `distribute_gas`'s per-index gas assignment at line 680 cannot be invoked twice for the same action, and the `assert_eq!` totality invariant at line 694 cannot be violated via this path.

### Citations

**File:** runtime/runtime/src/receipt_manager.rs (L86-98)
```rust
    /// Appends an action and returns the index the action was inserted in the receipt
    fn append_action(&mut self, receipt_index: ReceiptIndex, action: Action) -> usize {
        let actions = &mut self
            .action_receipts
            .get_mut(receipt_index as usize)
            .expect("receipt index should be present")
            .actions;

        actions.push(action);

        // Return index that action was inserted at
        actions.len() - 1
    }
```

**File:** runtime/runtime/src/receipt_manager.rs (L134-136)
```rust
        let new_receipt_index = self.action_receipts.len() as ReceiptIndex;
        self.action_receipts.push(new_receipt);
        Ok(new_receipt_index)
```

**File:** runtime/runtime/src/receipt_manager.rs (L378-394)
```rust
        let action_index = self.append_action(
            receipt_index,
            Action::FunctionCall(Box::new(FunctionCallAction {
                method_name: String::from_utf8(method_name)
                    .map_err(|_| HostError::InvalidMethodName)?,
                args,
                gas: prepaid_gas,
                deposit: attached_deposit,
            })),
        );

        if gas_weight.0 > 0 {
            self.gas_weights.push((
                FunctionCallActionIndex { receipt_index: receipt_index as usize, action_index },
                gas_weight,
            ));
        }
```
