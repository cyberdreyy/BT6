### No vulnerability found for this question.

**Analysis:**

The check at `stackerdb_listener.rs` around line 501-513 is: [1](#0-0) 

The `signer_pubkey` compared against `rejected_pubkey` is not derived from the message body itself — it is resolved earlier in the loop from the `signer_entry`/`slot_id` that owns the StackerDB slot the message was gossiped on (the code around line 380 parses this from the registered reward-set entry, independent of any field inside the `BlockRejection` struct): [2](#0-1) 

`recover_public_key()` performs ECDSA public-key recovery from `(hash(self.signer_signature_hash), self.signature)`. ECDSA recovery is a deterministic function of the exact message hash and the (r, s, recovery-id) signature components. If an attacker copies a genuine `signature` produced over `hash(H')` and swaps in `signer_signature_hash: H`, then at verification time the listener recomputes `hash(H)` (a different scalar) and feeds it with the same `(r, s)` into the recovery formula. Standard elliptic-curve math guarantees the recovered point will be an essentially random, unrelated public key (any successful match against a specific known key would require solving the discrete-log/signature-forgery problem, not simple field reordering). Consequently `rejected_pubkey != signer_pubkey` fires with overwhelming probability and the forged message is dropped exactly as the guard intends.

This is precisely the binding the design relies on: `consensus_serialize` writing the two fields independently is irrelevant, because the signature is verified against a hash that is *recomputed at verify time* from the (attacker-controlled) `signer_signature_hash` field, and ECDSA recovery makes the signature-to-message binding cryptographically tight, not merely structural. There is no bypass — the recovered key equality check holds before and after the field mutation, so no signer-impersonated rejection can be injected, and no chainstate/liveness property is broken by this construction.

### Citations

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L378-385)
```rust
                let Ok(signer_pubkey) = StacksPublicKey::from_slice(&signer_entry.signing_key)
                else {
                    return Err(NakamotoNodeError::SignerSignatureError(
                        "Failed to parse signer public key".into(),
                    ));
                };

                match message {
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
