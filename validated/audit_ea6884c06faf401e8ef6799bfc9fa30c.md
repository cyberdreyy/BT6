### Title
Unsigned `BlockResponseData` fields let anyone forge a signer's tenure-extend/read-count timestamp while replaying a legitimate signature - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
`BlockAccepted`/`BlockResponse::Accepted` signatures are computed and verified only over `signer_signature_hash` — never over `response_data` (`tenure_extend_timestamp`, `tenure_extend_read_count_timestamp`, `reject_reason`, `failed_txid`). The coordinator (`StackerDBListener`) trusts these unsigned fields as if they were authenticated statements from the signer, using them to drive the miner's tenure-extension and read-count-idle-timeout logic. This is the same bug class as the Term Finance report: a signature is checked against only part of the message, while other operative data travels alongside it unauthenticated and is treated as if it had been signed.

### Finding Description
`BlockAccepted::new`/`BlockResponse::accepted` build a struct containing `signer_signature_hash`, `signature`, `metadata`, and `response_data: BlockResponseData` (which carries `tenure_extend_timestamp` and `tenure_extend_read_count_timestamp`): [1](#0-0) 

The signer's own signature is over `block_hash.bits()` only: [2](#0-1) 

And on the node/coordinator side, the exact same narrow check is performed: [3](#0-2) 

Despite the signature covering only `block_sighash`, the coordinator immediately extracts and trusts `response_data.tenure_extend_timestamp` / `response_data.tenure_extend_read_count_timestamp` from the *same message* and feeds them into per-signer bookkeeping that influences the miner's tenure-extension decision: [4](#0-3) [5](#0-4) 

Because `response_data` is never included in the signed digest, any party that has previously observed a valid `(block_sighash, signature)` pair from a signer — a StackerDB peer, a relaying node, or the signer's own gossip infrastructure — can re-wrap that same signature with an arbitrarily different `response_data` payload (e.g. a `tenure_extend_timestamp` of `0` or `u64::MAX`) and rebroadcast it. `StacksMessageCodec` deserialization and the verification code shown above will accept it as a genuine, unmodified acceptance from that signer, because `signer_pubkey.verify(block_sighash.bits(), &signature)` only checks the hash, and the hash does not commit to `response_data`. There is no `usedNonces`-style replay guard here either: the check at line 443 in `stackerdb_listener.rs` (`!block.gathered_signatures.contains_key(&slot_id)`) only prevents re-counting the *signing weight*, but `update_idle_timestamp` / `update_read_count_timestamp` are still called unconditionally on every accepted message for that slot, regardless of whether the signature/weight was already counted.

### Impact Explanation
This breaks the equality between "what a signer actually signed" and "what the coordinator treats as validated, signer-attributed data." An attacker with no majority, no signer key, and no auth_token can inject a forged `tenure_extend_timestamp`/`tenure_extend_read_count_timestamp` attributed to any signer whose valid `(hash, signature)` pair it has observed (trivially available from StackerDB history or the wire). Since these timestamps drive the miner's tenure-idle-extension and read-count-idle-extension timeout computations, an attacker can skew them to force premature or delayed tenure extension decisions — a liveness wedge affecting block production timing that is falsely attributed to a specific, non-consenting signer. This matches the "High" impact bucket: manipulating state that influences tenure/timeout behavior via forged, unauthenticated signer-attributed data, achievable by a lone external actor.

### Likelihood Explanation
High. No cryptographic material or majority coordination is required — only observation of one previously broadcast `BlockAccepted` message for the block in question (these are gossiped openly over StackerDB to all participants and observers) and the ability to submit a new StackerDB chunk/message with modified `response_data` and the reused signature/hash pair.

### Recommendation
Include `response_data` (or a hash/commitment of it) inside the signed digest for `BlockAccepted` responses, the same way `BlockRejection::hash()` already binds `signer_signature_hash` via `make_structured_data_domain`/`structured_data_message_hash`. Verify the full struct — not just `signer_signature_hash` — both in `stacks-signer/src/v0/signer.rs::handle_block_signature` and in `stacks-node/src/nakamoto_node/stackerdb_listener.rs`, and reject any message whose `response_data` cannot be shown to have been produced by the signing key.

### Proof of Concept
1. Observe a legitimate `SignerMessage::BlockResponse(BlockResponse::Accepted(BlockAccepted { signer_signature_hash: H, signature: S, response_data: D, .. }))` broadcast by signer `X` for block `H` (visible to any StackerDB reader).
2. Construct a new `BlockAccepted { signer_signature_hash: H, signature: S, metadata: ..., response_data: D' }` where `D'` sets `tenure_extend_timestamp` / `tenure_extend_read_count_timestamp` to attacker-chosen extreme values, reusing the same `S`.
3. Publish this crafted message to the StackerDB slot (or any channel the coordinator listens on).
4. `StackerDBListener` verifies `signer_pubkey.verify(H.bits(), S)` — which still succeeds because `S` was a valid signature over `H` — and then unconditionally calls `update_idle_timestamp`/`update_read_count_timestamp` with the forged `D'` values, attributing them to signer `X` even though `X` never produced or approved `D'`. [6](#0-5)

### Citations

**File:** libsigner/src/v0/messages.rs (L1656-1666)
```rust
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct BlockAccepted {
    /// The signer signature hash of the block that was accepted
    pub signer_signature_hash: Sha512Trunc256Sum,
    /// The signer's signature across the acceptance
    pub signature: MessageSignature,
    /// Signer message metadata
    pub metadata: SignerMessageMetadata,
    /// Extra versioned block response data
    pub response_data: BlockResponseData,
}
```

**File:** stacks-signer/src/v0/signer.rs (L2388-2399)
```rust

        // recover public key
        let Ok(public_key) = Secp256k1PublicKey::recover_to_pubkey_without_validating_low_s(
            block_hash.bits(),
            signature,
        ) else {
            debug!("{self}: Received unrecovarable signature. Will not store.";
                   "signature" => %signature,
                   "signer_signature_hash" => %block_hash);

            return;
        };
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L386-396)
```rust
                    SignerMessageV0::BlockResponse(BlockResponse::Accepted(accepted)) => {
                        let BlockAccepted {
                            signer_signature_hash: block_sighash,
                            signature,
                            metadata,
                            response_data,
                        } = accepted;
                        let tenure_extend_timestamp = response_data.tenure_extend_timestamp;
                        let read_count_extend_timestamp =
                            response_data.tenure_extend_read_count_timestamp;

```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L411-484)
```rust
                        let Ok(valid_sig) = signer_pubkey.verify(block_sighash.bits(), &signature)
                        else {
                            warn!(
                                "StackerDBListener: Got invalid signature from a signer. Ignoring."
                            );
                            continue;
                        };
                        if !valid_sig {
                            warn!(
                                "StackerDBListener: Processed signature but didn't validate over the expected block. Ignoring";
                                "signature" => %signature,
                                "signer_signature_hash" => %block_sighash,
                                "slot_id" => slot_id,
                            );
                            continue;
                        }

                        if Self::fault_injection_ignore_signatures() {
                            warn!("StackerDBListener: fault injection: ignoring well-formed signature for block";
                                "signer_signature_hash" => %block_sighash,
                                "signer_pubkey" => signer_pubkey.to_hex(),
                                "signer_slot_id" => slot_id,
                                "signature" => %signature,
                                "signer_weight" => signer_entry.weight,
                                "total_weight_approved" => block.total_weight_approved,
                                "percent_approved" => block.total_weight_approved as f64 / self.total_weight as f64 * 100.0,
                                "total_weight_rejected" => block.total_weight_rejected,
                                "percent_rejected" => block.total_weight_rejected as f64 / self.total_weight as f64 * 100.0,
                            );
                            continue;
                        }

                        if !block.gathered_signatures.contains_key(&slot_id) {
                            block.total_weight_approved = block
                                .total_weight_approved
                                .saturating_add(signer_entry.weight);

                            info!("StackerDBListener: Signature Added to block";
                                "signer_signature_hash" => %block_sighash,
                                "signer_pubkey" => signer_pubkey.to_hex(),
                                "signer_slot_id" => slot_id,
                                "signature" => %signature,
                                "signer_weight" => signer_entry.weight,
                                "total_weight_approved" => block.total_weight_approved,
                                "percent_approved" => block.total_weight_approved as f64 / self.total_weight as f64 * 100.0,
                                "total_weight_rejected" => block.total_weight_rejected,
                                "percent_rejected" => block.total_weight_rejected as f64 / self.total_weight as f64 * 100.0,
                                "weight_threshold" => self.weight_threshold,
                                "tenure_extend_timestamp" => tenure_extend_timestamp,
                                "read_count_extend_timestamp" => read_count_extend_timestamp,
                                "server_version" => metadata.server_version,
                            );
                        }
                        block.gathered_signatures.insert(slot_id, signature);
                        block.responded_signers.insert(slot_id);

                        if block.total_weight_approved >= self.weight_threshold {
                            // Signal to anyone waiting on this block that we have enough signatures
                            cvar.notify_all();
                        }

                        // Update the idle timestamp for this signer
                        self.update_idle_timestamp(
                            signer_pubkey.clone(),
                            tenure_extend_timestamp,
                            signer_entry.weight,
                        );

                        // Update the read-count timestamp for this signer
                        self.update_read_count_timestamp(
                            signer_pubkey,
                            read_count_extend_timestamp,
                            signer_entry.weight,
                        );
```
