### Title
`StackerDBListener::run` hard-errors and tears down the entire signature-aggregation thread on a single unrecognized `slot_id`, instead of skipping that one message - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
In the per-message loop of `StackerDBListener::run`, a `slot_id` that is not present in `self.signer_entries` causes the function to `return Err(NakamotoNodeError::SignerSignatureError(...))`, propagating out of `run()` and killing the whole listener/coordinator thread, whereas every other "message doesn't match current state" condition in the same loop (unknown block hash, bad pubkey recovery, invalid signature) is handled with `continue` to simply drop that one message.

### Finding Description
The equality the loop is supposed to maintain is: *for a `BlockResponse` message tagged with `slot_id`, either `slot_id` is a currently active signer in `self.signer_entries`, in which case it is processed, or it is not, in which case the individual message is ignored and processing continues for the rest of the batch.* This equality is broken at: [1](#0-0) 

```rust
for (slot_id, _pk, message) in messages.into_iter() {
    let Some(signer_entry) = &self.signer_entries.get(&slot_id) else {
        return Err(NakamotoNodeError::SignerSignatureError(
            "Signer entry not found".into(),
        ));
    };
```

Every other similarly-shaped guard in the same loop body treats an unexpected/unmatched condition as recoverable and uses `continue` rather than `return Err`, e.g. unknown block hash [2](#0-1) , invalid signature [3](#0-2) , and rejected-data pubkey mismatch [4](#0-3) . The `signer_entries` lookup is the sole exception, and it is checked first, before any signature or content validation of the message — meaning it is the cheapest possible way to hit the fatal path for anyone who can get a chunk onto the relevant slot range.

The `signer_entries` map is populated once at construction from the reward-set snapshot for the current cycle/signer_set [5](#0-4) , and is never re-derived during `run()`. The dispatch loop filters incoming events only by contract-name prefix/boot-ness [6](#0-5)  and by `signer_set` parity (0/1, i.e. `reward_cycle_id % 2`) [7](#0-6) , not by validating that every `modified_slots` entry actually falls within the current `signer_entries` key set before dispatching messages into the per-message loop. So any chunk write that reaches this listener with a `slot_id` outside the current, possibly-shrunk, signer set will hit the hard-error branch, before signature verification can even reject it as unauthenticated.

I was not able to fully verify, within the scope of this repo and excluding out-of-scope StackerDB transport/write-authorization mechanics, whether the underlying StackerDB chunk-write authorization strictly prevents a non-current-cycle signer from ever getting such a chunk accepted/dispatched as an event for the *current* cycle's contract instance in all cases (e.g., replica reuse across same-parity cycles, or event-generation races at cycle boundaries). That specific transport/authorization detail is explicitly out of scope per the rules. However, the application-layer defect — treating a single unmatched `slot_id` as loop-fatal instead of per-message-skippable — is a genuine, in-scope logic bug in `stackerdb_listener.rs` independent of how the mismatched slot_id chunk arrives.

### Impact Explanation
This breaks a bounded-liveness guarantee: an isolated malformed/mismatched slot reference should only cause that one message to be dropped, not terminate the entire coordinator thread that aggregates signer `BlockResponse` signatures for the current tenure. If the thread returns `Err` and dies, the miner node can no longer collect signatures/rejections for any block in that tenure via this path, stalling block finalization for as long as the thread remains down (i.e., until the coordinator/miner logic restarts it, if it does). This matches the "High: signer/coordinator wedged into never signing/aggregating valid blocks" liveness category rather than a chain-safety break — no invalid or non-canonical block is ever accepted, only availability of the aggregation pipeline is affected.

### Likelihood Explanation
Preconditions: a `StackerDBChunksEvent` for the active signer contract with a `modified_slots` entry whose `slot_id` is not a key in the constructed `self.signer_entries` (e.g., because the reward set shrank between cycles and a slot from the larger cycle is referenced). The `signer_entries` map is fixed at listener construction and never revalidated per event [8](#0-7) , so any event that reaches the per-message loop with such a slot_id will unconditionally hit the fatal branch, which is checked before any other validation. The main open question — whether an attacker with only one signer's slot/weight can get such a chunk to actually surface as a dispatched event for the current listener instance — depends on StackerDB replica/authorization mechanics that are out of this audit's scope, so full end-to-end attacker feasibility could not be conclusively confirmed here; but the code-level asymmetry (fatal vs. skip) is present and reproducible in isolation regardless of delivery mechanism.

### Recommendation
Change the missing-`signer_entries` branch to log and `continue` (drop only the offending message) rather than returning `Err` from `run()`, matching the pattern already used for every other unmatched/invalid condition in the loop:

```rust
let Some(signer_entry) = self.signer_entries.get(&slot_id) else {
    warn!("StackerDBListener: Received message for unknown slot_id. Ignoring."; "slot_id" => slot_id);
    continue;
};
```

### Proof of Concept
```rust
// stacks-node/src/nakamoto_node/stackerdb_listener.rs (test module)
#[test]
fn unknown_slot_id_should_not_kill_listener_loop() {
    // Construct a StackerDBListener (or a minimal harness reproducing the loop body)
    // with signer_entries containing only slot_ids {0, 1} (simulating a shrunk reward set).
    let mut listener = build_test_listener(/* signer_entries keys: 0,1 */);

    // Craft a StackerDBChunksEvent for the signer contract whose modified_slots
    // includes slot_id = 5 (valid in a prior, larger reward set, but absent now),
    // carrying a well-formed BlockResponse::Accepted message.
    let event = build_signer_event_with_slot_id(5, block_response_accepted_msg());

    listener.inject_event_for_test(event);

    // BEFORE fix: listener.run() one iteration returns
    //   Err(NakamotoNodeError::SignerSignatureError("Signer entry not found"))
    // asserting this demonstrates the wedge.
    let result = listener.process_one_event_for_test();
    assert!(matches!(result, Err(NakamotoNodeError::SignerSignatureError(_))),
        "current code hard-errors on unknown slot_id");

    // AFTER fix: the same call should return Ok(()) and simply skip the message,
    // leaving the listener able to process subsequent valid messages/events.
}
```

### Citations

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L221-239)
```rust
        let Some(reward_set_signers) = reward_set.signers() else {
            error!("Could not initialize signing coordinator for reward set without signer");
            debug!("reward set: {reward_set:?}");
            return Err(ChainstateError::NoRegisteredSigners(0));
        };

        let signer_entries = reward_set_signers
            .iter()
            .cloned()
            .enumerate()
            .map(|(idx, signer)| {
                let Ok(slot_id) = u32::try_from(idx) else {
                    return Err(ChainstateError::InvalidStacksBlock(
                        "Signer index exceeds u32".into(),
                    ));
                };
                Ok((slot_id, signer))
            })
            .collect::<Result<HashMap<_, _>, ChainstateError>>()?;
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L268-283)
```rust
        Ok(Self {
            stackerdb_channel,
            receiver: Some(receiver),
            node_keep_running,
            keep_running,
            signer_set,
            total_weight,
            weight_threshold,
            signer_entries,
            blocks: Arc::new((Mutex::new(HashMap::new()), Condvar::new())),
            signer_idle_timestamps: Arc::new(Mutex::new(HashMap::new())),
            global_state_evaluator: Arc::new(Mutex::new(global_state_evaluator)),
            is_mainnet: config.is_mainnet(),
            signer_read_count_timestamps: Arc::new(Mutex::new(HashMap::new())),
        })
    }
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L331-338)
```rust
            // check to see if this event we got is a signer event
            let is_signer_event =
                event.contract_id.name.starts_with(SIGNERS_NAME) && event.contract_id.is_boot();

            if !is_signer_event {
                debug!("StackerDBListener: Ignoring StackerDB event for non-signer contract"; "contract" => %event.contract_id);
                continue;
            }
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L356-361)
```rust
            if signer_set != self.signer_set {
                debug!(
                    "StackerDBListener: Received signer event for other reward cycle. Ignoring."
                );
                continue;
            };
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L372-377)
```rust
            for (slot_id, _pk, message) in messages.into_iter() {
                let Some(signer_entry) = &self.signer_entries.get(&slot_id) else {
                    return Err(NakamotoNodeError::SignerSignatureError(
                        "Signer entry not found".into(),
                    ));
                };
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L400-409)
```rust
                        let Some(block) = blocks.get_mut(&block_sighash) else {
                            info!(
                                "StackerDBListener: Received signature for block that we did not request. Ignoring.";
                                "signature" => %signature,
                                "signer_signature_hash" => %block_sighash,
                                "slot_id" => slot_id,
                                "signer_set" => self.signer_set,
                            );
                            continue;
                        };
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L411-417)
```rust
                        let Ok(valid_sig) = signer_pubkey.verify(block_sighash.bits(), &signature)
                        else {
                            warn!(
                                "StackerDBListener: Got invalid signature from a signer. Ignoring."
                            );
                            continue;
                        };
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L501-513)
```rust
                        let rejected_pubkey = match rejected_data.recover_public_key() {
                            Ok(rejected_pubkey) => {
                                if rejected_pubkey != signer_pubkey {
                                    warn!("StackerDBListener: Recovered public key from rejected data does not match signer's public key. Ignoring.");
                                    continue;
                                }
                                rejected_pubkey
                            }
                            Err(e) => {
                                warn!("StackerDBListener: Failed to recover public key from rejected data: {e:?}. Ignoring.");
                                continue;
                            }
                        };
```
