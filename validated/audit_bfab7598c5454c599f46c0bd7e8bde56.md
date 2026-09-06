#No vulnerability found for this question.

**Reasoning:** The `StateMachineUpdate::BurnBlock` pending state and its resolution in `handle_pending_update`/`bitcoin_block_arrival` (`stacks-signer/src/v0/signer_state.rs`) is driven exclusively by `SignerEvent::NewBurnBlock`, which is not a StackerDB/gossip message but an HTTP callback delivered from the signer's own trusted stacks-node event-observer (`/new_burn_block` path) to the signer's local listening endpoint. [1](#0-0) [2](#0-1) [3](#0-2) 

The event is produced by the node itself when it processes a real burnchain block via `announce_burn_block`/`process_burn_block`, and the resolution check in `bitcoin_block_arrival` compares the pending expectation against `client.get_peer_info()` — i.e., the signer's own trusted node's view of the burnchain tip, not anything supplied by a remote party: [4](#0-3) [5](#0-4) 

The attacker model in this question is restricted to winning a single miner slot and gossiping StackerDB/signer messages — it does not include the ability to forge HTTP POSTs to the `/new_burn_block` endpoint of a victim signer's local event-observer socket, which would require local network/host access to the signer (out of scope per the rules). Winning a Bitcoin miner slot produces a real, valid burn block that the victim's own node will process and correctly report via `get_peer_info`/`announce_burn_block`, so it cannot manufacture a burn block whose `consensus_hash`/height never resolves. Therefore the claimed "attacker gossips a `NewBurnBlock` event" step is not reachable by an unprivileged attacker under the stated threat model, and the wedge described does not have an in-scope exploitation path.

### Citations

**File:** libsigner/src/events.rs (L437-448)
```rust
            if request.url() == "/stackerdb_chunks" {
                process_event::<T, StackerDBChunksEvent>(request)
            } else if request.url() == "/proposal_response" {
                process_event::<T, BlockValidateResponse>(request)
            } else if request.url() == "/new_burn_block" {
                process_event::<T, BurnBlockEvent>(request)
            } else if request.url() == "/shutdown" {
                event_receiver.stop_signal.store(true, Ordering::SeqCst);
                Err(EventError::Terminated)
            } else if request.url() == "/new_block" {
                process_event::<T, StacksBlockEvent>(request)
            } else {
```

**File:** libsigner/src/events.rs (L637-649)
```rust
impl<T: SignerEventTrait> TryFrom<BurnBlockEvent> for SignerEvent<T> {
    type Error = EventError;

    fn try_from(burn_block_event: BurnBlockEvent) -> Result<Self, Self::Error> {
        Ok(SignerEvent::NewBurnBlock {
            burn_height: burn_block_event.burn_block_height,
            received_time: SystemTime::now(),
            burn_header_hash: burn_block_event.burn_block_hash,
            consensus_hash: burn_block_event.consensus_hash,
            parent_burn_block_hash: burn_block_event.parent_burn_block_hash,
        })
    }
}
```

**File:** stacks-node/src/event_dispatcher.rs (L1298-1300)
```rust
    fn send_new_burn_block(&self, event_observer: &EventObserver, payload: &serde_json::Value) {
        self.dispatch_to_observer_or_log_error(event_observer, payload, PATH_BURN_BLOCK_SUBMIT);
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

**File:** stacks-signer/src/v0/signer.rs (L630-672)
```rust
                burn_height,
                burn_header_hash,
                consensus_hash,
                received_time,
                parent_burn_block_hash,
            } => {
                info!("{self}: Received a new burn block event for block height {burn_height}");
                self.signer_db
                    .insert_burn_block(
                        burn_header_hash,
                        consensus_hash,
                        *burn_height,
                        received_time,
                        parent_burn_block_hash,
                    )
                    .unwrap_or_else(|e| {
                        error!(
                            "Failed to write burn block event to signerdb";
                            "err" => ?e,
                            "burn_header_hash" => %burn_header_hash,
                            "burn_height" => burn_height
                        );
                        panic!("{self} Failed to write burn block event to signerdb: {e}");
                    });

                // A tenure this far below the tip can no longer conflict with a proposal, so we
                // no longer need to remember that we sanctioned reorging it.
                if let Err(e) = self
                    .signer_db
                    .prune_superseded_tenures(burn_height.saturating_sub(MAX_FORK_DEPTH))
                {
                    warn!("{self}: Failed to prune the superseded tenures: {e:?}");
                }

                let active_signer_protocol_version = self.get_signer_protocol_version();
                self.local_state_machine
                    .bitcoin_block_arrival(&mut self.signer_db, stacks_client, &self.proposal_config, Some(NewBurnBlock {
                        burn_block_height: *burn_height,
                        consensus_hash: consensus_hash.clone(),
                    }),
                    &mut self.tx_replay_scope
                , &self.global_state_evaluator, active_signer_protocol_version)
                    .unwrap_or_else(|e| error!("{self}: failed to update local state machine for latest bitcoin block arrival"; "err" => ?e));
```
