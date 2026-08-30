[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** runtime/runtime/src/receipt_manager.rs (L111-137)
```rust
    pub(super) fn create_action_receipt(
        &mut self,
        input_data_ids: Vec<CryptoHash>,
        receipt_indices: Vec<ReceiptIndex>,
        receiver_id: AccountId,
    ) -> Result<ReceiptIndex, VMLogicError> {
        assert_eq!(input_data_ids.len(), receipt_indices.len());
        for (data_id, receipt_index) in input_data_ids.iter().zip(receipt_indices.into_iter()) {
            self.action_receipts
                .get_mut(receipt_index as usize)
                .ok_or(HostError::InvalidReceiptIndex { receipt_index })?
                .output_data_receivers
                .push(DataReceiver { data_id: *data_id, receiver_id: receiver_id.clone() });
        }

        let new_receipt = ActionReceiptMetadata {
            receiver_id,
            refund_to: None,
            output_data_receivers: vec![],
            input_data_ids,
            actions: vec![],
            is_promise_yield: false,
        };
        let new_receipt_index = self.action_receipts.len() as ReceiptIndex;
        self.action_receipts.push(new_receipt);
        Ok(new_receipt_index)
    }
```

**File:** runtime/runtime/src/receipt_manager.rs (L478-508)
```rust
    pub(super) fn append_action_add_gas_key_with_function_call(
        &mut self,
        receipt_index: ReceiptIndex,
        public_key: PublicKey,
        num_nonces: NonceIndex,
        allowance: Option<Balance>,
        receiver_id: AccountId,
        method_names: Vec<Vec<u8>>,
    ) -> Result<(), VMLogicError> {
        self.append_action(
            receipt_index,
            Action::AddKey(Box::new(AddKeyAction {
                public_key,
                access_key: AccessKey::gas_key_function_call(
                    num_nonces,
                    FunctionCallPermission {
                        allowance,
                        receiver_id: receiver_id.into(),
                        method_names: method_names
                            .into_iter()
                            .map(|method_name| {
                                String::from_utf8(method_name)
                                    .map_err(|_| HostError::InvalidMethodName)
                            })
                            .collect::<std::result::Result<Vec<_>, _>>()?,
                    },
                ),
            })),
        );
        Ok(())
    }
```

**File:** runtime/runtime/src/receipt_manager.rs (L544-551)
```rust
    pub(super) fn append_action_add_key_with_full_access(
        &mut self,
        receipt_index: ReceiptIndex,
        public_key: PublicKey,
        nonce: Nonce,
    ) {
        self.append_action(
            receipt_index,
```
