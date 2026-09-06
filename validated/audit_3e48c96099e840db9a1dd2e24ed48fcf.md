[1](#0-0) [2](#0-1)

### Citations

**File:** stacks-signer/src/v0/signer_state.rs (L584-596)
```rust
                match update {
                    StateMachineUpdate::BurnBlock(pending_burn_block) => {
                        match expected_burn_block {
                            None => expected_burn_block = Some(pending_burn_block),
                            Some(ref expected) => {
                                if pending_burn_block.burn_block_height > expected.burn_block_height
                                {
                                    expected_burn_block = Some(pending_burn_block);
                                }
                            }
                        }
                    }
                }
```

**File:** stacks-signer/src/v0/signer_state.rs (L602-628)
```rust
        let peer_info = client.get_peer_info()?;
        let next_burn_block_height = peer_info.burn_block_height;
        let next_burn_block_hash = peer_info.pox_consensus;
        let mut tx_replay_set = prior_state_machine.tx_replay_set.clone();

        if let Some(expected_burn_block) = expected_burn_block {
            // If the next height is less than the expected height, we need to wait.
            // OR if the next height is the same, but with a different hash, we need to wait.
            let node_behind_expected =
                next_burn_block_height < expected_burn_block.burn_block_height;
            let node_on_equal_fork = next_burn_block_height
                == expected_burn_block.burn_block_height
                && next_burn_block_hash != expected_burn_block.consensus_hash;
            if node_behind_expected || node_on_equal_fork {
                let err_msg = format!(
                    "Node has not processed the next burn block yet. Expected height = {}, Expected consensus hash = {}, Node height = {}, Node consensus hash = {}",
                    expected_burn_block.burn_block_height,
                    expected_burn_block.consensus_hash,
                    next_burn_block_height,
                    next_burn_block_hash,
                );
                *self = Self::Pending {
                    update: StateMachineUpdate::BurnBlock(expected_burn_block),
                    prior: prior_state_machine,
                };
                return Err(ClientError::InvalidResponse(err_msg).into());
            }
```
