### Title
Denial-of-Service: a single malformed/unrecognized signer `slot_id` fatally kills the miner's `StackerDBListener`, wedging block-signing liveness - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
`StackerDBListener::run()` is the miner/coordinator's event loop that consumes `BlockResponse`/`BlockPreCommit`/`StateMachineUpdate` messages gossiped by signers over StackerDB and tallies signature/rejection weight toward the block-approval threshold. For almost every anomaly it encounters (unknown block hash, bad recovered pubkey, mismatched signature, wrong signer set, etc.) the loop logs and `continue`s to the next message. But for two specific conditions — an unrecognized `slot_id` and an unparsable `signing_key` in `signer_entries` — it instead does `return Err(...)`, which terminates the entire `run()` loop (and the thread hosting it) instead of skipping just the one bad message. This is directly analogous to the `restify-paginate` bug class (CVE-2020-27543): an edge-case/absent input, instead of being handled defensively like its siblings, propagates into an uncaught failure that kills the whole service.

### Finding Description
In the per-message dispatch loop: [1](#0-0) 

```rust
for (slot_id, _pk, message) in messages.into_iter() {
    let Some(signer_entry) = &self.signer_entries.get(&slot_id) else {
        return Err(NakamotoNodeError::SignerSignatureError(
            "Signer entry not found".into(),
        ));
    };
    let Ok(signer_pubkey) = StacksPublicKey::from_slice(&signer_entry.signing_key)
    else {
        return Err(NakamotoNodeError::SignerSignatureError(
            "Failed to parse signer public key".into(),
        ));
    };
```

Every other defensive check in this same match statement (unknown block hash at [2](#0-1) , mismatched recovered pubkey at [3](#0-2) , invalid signature at [4](#0-3) ) treats the anomaly as an isolated, ignorable event: it logs and `continue`s. Only the `slot_id`-not-found and `signing_key`-unparsable branches escalate to a fatal `Err` that unwinds the whole `run()` call.

`self.signer_entries` is a fixed snapshot (`HashMap<u32, NakamotoSignerEntry>`) built once at `StackerDBListener::new()` from the reward-set signers active at coordinator construction time: [5](#0-4) 

Meanwhile, the reward-cycle/signer-set filter applied to incoming StackerDB events is only a **mod-2 parity** check, not an exact reward-cycle equality check: [6](#0-5) 

```rust
if signer_set != self.signer_set {
    debug!("StackerDBListener: Received signer event for other reward cycle. Ignoring.");
    continue;
}
```

`self.signer_set` itself is derived the same way: `u32::try_from(reward_cycle_id % 2)` ( [7](#0-6) ). Because the equality being enforced here is only "same parity", not "same reward cycle", any stray/late StackerDB chunk event that is still in flight from a different reward cycle sharing the same parity (e.g. gossip propagation delay across a reward-cycle boundary, StackerDB sync replaying an older chunk, or a coordinator instance briefly overlapping with a new one during handoff) can pass the `signer_set` filter yet carry a `slot_id` that does not exist in *this* listener's `signer_entries` (built from a different, possibly smaller/differently-composed, signer set). That one message is enough to hit the `else { return Err(...) }` branch and kill the loop.

This breaks the liveness guarantee that the miner's coordinator continuously and resiliently tallies signer votes for as long as the tenure is active: instead, one anomalous message — reachable purely through normal gossip/relay timing near a reward-cycle boundary, without needing any signer's private key or a majority of signers — silently terminates the entire listening thread. All *subsequent* legitimate pre-commits, signatures, and rejections from every signer are then simply never processed by the miner, because nothing restarts `run()` on this error path within the loop itself (it returns out to whatever called `.run()`, exiting the coordinator's threaded logic rather than resuming the poll loop).

### Impact Explanation
This matches the "High" bucket in-scope for this analog: a signer/coordinator wedged such that it never processes valid, ongoing signer traffic (a liveness wedge). Once the `StackerDBListener` thread has returned `Err` and exited, the miner's coordinator stops accumulating `total_weight_approved`/`total_weight_rejected` for any block, meaning no block proposal can ever cross `weight_threshold` again through this listener instance — mining/block-confirmation for the tenure stalls until a full coordinator/miner restart occurs. This is exactly the same bug class as the referenced advisory: an omitted/unusual input (a slot the service doesn't expect, analogous to a missing Host header) that a well-designed handler should treat as a no-op/ignorable case instead crashes the whole listening service.

### Likelihood Explanation
The trigger condition does not require compromising any signer's key or achieving a majority — it only requires one out-of-band StackerDB chunk event (from legitimate gossip/replication machinery, not from crafting a malicious payload) to be delivered to a `StackerDBListener` instance whose `signer_entries` snapshot doesn't include that `slot_id`. Given that: (1) the `signer_set` filter is coarse (parity-only) rather than an exact reward-cycle check, and (2) StackerDB is an eventually-consistent, gossip/relay-based system that explicitly tolerates delayed/out-of-order chunk delivery across nodes, the preconditions for a stale or foreign chunk event reaching this loop are realistic during normal reward-cycle rollover / coordinator-restart windows — not a purely theoretical corner case. I could not fully trace, within the available context, the exact temporal window in which the previous coordinator instance is torn down relative to when the new one begins consuming events (i.e., whether there's a hard barrier that guarantees no cross-cycle event can ever reach a live listener with mismatched entries); this is the main residual uncertainty in likelihood, but the code-level defect — asymmetric `return Err` vs. `continue` for what is otherwise treated as a benign/ignorable anomaly everywhere else in the same function — is a clear, provable design flaw independent of exactly how rare the trigger is.

### Recommendation
Make the `slot_id`-not-found and `signing_key`-unparsable branches consistent with every other defensive check in this loop: log at `warn!`/`info!` and `continue` to the next message instead of `return Err(...)`. If a genuinely fatal misconfiguration needs to be surfaced (e.g. the coordinator's own reward-set data is corrupt), that should be detected and asserted once at `StackerDBListener::new()` construction time, not inside the per-message hot loop where a single stray/foreign message can take down all future processing.

### Proof of Concept
1. Miner boots a `StackerDBListener` for reward cycle `N` with `signer_entries` sized to the reward-cycle-`N` signer set (say slots `0..k`).
2. Due to normal StackerDB gossip/replication (eventually-consistent by design, per the module's own documentation — see `stackslib/src/net/stackerdb/mod.rs` header docs on store-and-forward propagation), a chunk event for a `.signers-<parity>-<msg_id>` contract from a different reward cycle sharing the same `reward_cycle % 2` parity, but with a larger signer set (or different composition), is delivered to this listener's `receiver`.
3. `signer_set != self.signer_set` check (line 356) passes because parity matches.
4. The message's `slot_id` (e.g. `k+1`) is looked up in `self.signer_entries` (built only for slots `0..k`) and is not found:
   `let Some(signer_entry) = &self.signer_entries.get(&slot_id) else { return Err(...) }` (lines 373–376).
5. `run()` returns `Err(NakamotoNodeError::SignerSignatureError(...))`, terminating the coordinator's event loop.
6. All subsequent `BlockResponse`/`BlockPreCommit` messages from every legitimate signer for the remainder of the tenure are never processed by this miner instance — no further block can reach `weight_threshold` through this listener, wedging block production until manual restart.

### Citations

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L218-219)
```rust
        let signer_set =
            u32::try_from(reward_cycle_id % 2).expect("FATAL: reward cycle id % 2 exceeds u32");
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L227-239)
```rust
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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L356-361)
```rust
            if signer_set != self.signer_set {
                debug!(
                    "StackerDBListener: Received signer event for other reward cycle. Ignoring."
                );
                continue;
            };
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L372-383)
```rust
            for (slot_id, _pk, message) in messages.into_iter() {
                let Some(signer_entry) = &self.signer_entries.get(&slot_id) else {
                    return Err(NakamotoNodeError::SignerSignatureError(
                        "Signer entry not found".into(),
                    ));
                };
                let Ok(signer_pubkey) = StacksPublicKey::from_slice(&signer_entry.signing_key)
                else {
                    return Err(NakamotoNodeError::SignerSignatureError(
                        "Failed to parse signer public key".into(),
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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L500-513)
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
