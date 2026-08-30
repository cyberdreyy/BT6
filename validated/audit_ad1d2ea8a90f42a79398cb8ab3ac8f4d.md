No vulnerability found for this question.

**Analysis:** The concern hypothesizes that `num_forwarded` could exceed the actual number of receipts placed into `outgoing_receipts`, causing `pop_n` to delete un-forwarded receipts. Tracing `forward_from_buffer_to_shard` shows this can't happen: the loop iterates the buffer strictly front-to-back via `iter()`, increments `num_forwarded` only inside the `ReceiptForwarding::Forwarded` arm, and immediately `break`s on the first `ReceiptForwarding::NotForwarded` result [1](#0-0) . Because of this break-on-first-failure design, the forwarded set is always exactly the contiguous prefix `first_index .. first_index+num_forwarded`, matching the keys `pop_n` removes at `core/store/src/trie/receipts_column_helper.rs:200-203` [2](#0-1) . There is no code path where a later item is skipped while an earlier untried item remains, or where `num_forwarded` could be inflated beyond receipts actually pushed to `outgoing_receipts` in `try_forward` [3](#0-2) . Any `StorageError` from the iterator propagates via `?` before `pop_n` is reached, aborting the whole apply rather than corrupting the queue. The invariant "keys removed by pop_n == keys whose receipts were actually forwarded" holds by construction of this specific call site, independent of any attacker-influenced congestion parameters (which only affect *how many* receipts get forwarded, not the correspondence between the count and the removed indices).

### Citations

**File:** runtime/runtime/src/congestion_control.rs (L345-383)
```rust
        let mut num_forwarded = 0;
        let mut outgoing_metadatas_updates: Vec<(ByteSize, Gas)> = Vec::new();
        for receipt_result in
            self.outgoing_buffers.to_shard(buffer_shard_id).iter(&state_update.trie, true)
        {
            let receipt = receipt_result?;
            let gas = receipt_congestion_gas(&receipt, &apply_state.config)?;
            let size = receipt_size(&receipt)?;
            let should_update_outgoing_metadatas = receipt.should_update_outgoing_metadatas();
            let receipt = receipt.into_receipt();
            let target_shard_id = receipt.receiver_shard_id(&shard_layout)?;

            match Self::try_forward(
                receipt,
                gas,
                size,
                target_shard_id,
                &mut self.outgoing_limit,
                &mut self.outgoing_receipts,
                apply_state,
                &mut self.stats,
            )? {
                ReceiptForwarding::Forwarded => {
                    self.own_congestion_info.remove_receipt_bytes(size)?;
                    self.own_congestion_info.remove_buffered_receipt_gas(gas.as_gas().into())?;
                    if should_update_outgoing_metadatas {
                        // Can't update metadatas immediately because state_update is borrowed by iterator.
                        outgoing_metadatas_updates.push((ByteSize::b(size), gas));
                    }
                    // count how many to release later to avoid modifying
                    // `state_update` while iterating based on
                    // `state_update.trie`.
                    num_forwarded += 1;
                }
                ReceiptForwarding::NotForwarded(_) => {
                    break;
                }
            }
        }
```

**File:** runtime/runtime/src/congestion_control.rs (L443-462)
```rust
        let admission_gas = if ProtocolFeature::ClampOutgoingGasAdmission
            .enabled(apply_state.current_protocol_version)
        {
            gas.min(apply_state.config.congestion_control_config.allowed_shard_outgoing_gas)
        } else {
            gas
        };

        if forward_limit.gas >= admission_gas && forward_limit.size >= size {
            tracing::trace!(target: "runtime", ?shard, receipt_id=?receipt.receipt_id(), "forwarding buffered receipt");
            outgoing_receipts.push(receipt);
            forward_limit.gas = forward_limit.gas.saturating_sub(gas);
            forward_limit.size -= size;
            stats.forwarded_receipts.entry(shard).or_default().add_receipt(size, gas);

            Ok(ReceiptForwarding::Forwarded)
        } else {
            tracing::trace!(target: "runtime", ?shard, receipt_id=?receipt.receipt_id(), "not forwarding buffered receipt");
            Ok(ReceiptForwarding::NotForwarded(receipt))
        }
```

**File:** core/store/src/trie/receipts_column_helper.rs (L191-213)
```rust
    fn pop_n(&mut self, state_update: &mut TrieUpdate, n: u64) -> Result<u64, StorageError> {
        self.debug_check_unchanged(state_update);

        let indices = self.indices();
        let to_remove = std::cmp::min(
            n,
            indices.next_available_index.checked_sub(indices.first_index).unwrap_or(0),
        );

        for index in indices.first_index..(indices.first_index + to_remove) {
            let key = self.trie_key(index);
            state_update.remove(key);
        }

        if to_remove > 0 {
            self.indices_mut().first_index = indices
                .first_index
                .checked_add(to_remove)
                .expect("first_index + to_remove should be < next_available_index");
            self.write_indices(state_update);
        }
        Ok(to_remove)
    }
```
