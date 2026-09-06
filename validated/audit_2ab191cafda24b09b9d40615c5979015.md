[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** stacks-signer/src/v0/signer.rs (L586-592)
```rust
                            }
                            self.handle_block_proposal(
                                stacks_client,
                                sortition_state,
                                block_proposal,
                            );
                        }
```

**File:** stacks-signer/src/v0/signer.rs (L881-882)
```rust
        let signer_signature_hash = block.header.signer_signature_hash();
        let block_id = block.block_id();
```

**File:** stacks-signer/src/v0/signer.rs (L897-907)
```rust
        // Check if proposal can be rejected now if not valid against sortition view
        if let Some(sortition_state) = sortition_state {
            match sortition_state.check_proposal(
                stacks_client,
                &mut self.signer_db,
                block,
                true,
                self.global_state_evaluator
                    .get_global_tx_replay_set()
                    .unwrap_or_default(),
            ) {
```

**File:** stacks-signer/src/v0/signer.rs (L974-975)
```rust
        // Check if proposal can be rejected now if not valid against the global state
        match global_state_view.check_proposal(stacks_client, &mut self.signer_db, block) {
```

**File:** stacks-signer/src/signerdb.rs (L1-1)
```rust
// Copyright (C) 2013-2020 Blockstack PBC, a public benefit corporation
```
