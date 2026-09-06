### Title
Miner-signature malleability lets one block have multiple valid identities (`signer_signature_hash`/`block_id`), breaking block-identity equality used by signer dedup/equivocation logic - (File: `stackslib/src/chainstate/nakamoto/mod.rs`)

### Summary
The Nakamoto block header commits the *miner's* signature into the hash that signers actually sign and that becomes the block's canonical identifier (`signer_signature_hash`/`block_hash`/`block_id`). Verification of that miner signature uses the "skip low-S" recovery function, i.e. it accepts the non-canonical, ECDSA-malleable form of a valid signature. Because ECDSA signature malleability (computing `s' = n - s` and flipping the recovery id) is a purely public operation that requires no private key, anyone who observes one canonically-signed block can derive a second, equally "valid" block with byte-identical consensus content (same `chain_length`, `consensus_hash`, `parent_block_id`, `tx_merkle_root`, `state_index_root`, `timestamp`, `pox_treatment`, `problematic_txs`) but a *different* `miner_signature`, hence a different `signer_signature_hash`/`block_id`. Every signer-side dedup, pre-commit, and duplicate-block mechanism is keyed by this hash, so the protocol's "one proposal ⇔ one identity" equality assumption breaks.

### Finding Description
`NakamotoBlockHeader::signer_signature_hash_inner` explicitly folds `self.miner_signature` into the digest that becomes the block's identity: [1](#0-0) 

`block_hash()`/`block_id()` are derived from that same preimage ("same as sighash -- we don't commit to signatures"): [2](#0-1) 

The miner signature is verified with the *low-S-skipping* recovery routine, both when a peer recovers the miner's key (`recover_miner_pk`, used by `check_miner_signature`) and when the on-chain signer-signature vector itself is re-derived in `verify_signer_signatures`: [3](#0-2) [4](#0-3) [5](#0-4) 

`recover_to_pubkey_without_validating_low_s` is documented as unsafe for new code precisely because it accepts the malleated (high-S) counterpart of any signature and still recovers the same public key: [6](#0-5) 

Because the miner's signature (not just a hash of the block) is embedded in the identity, and because that signature's canonical form is not enforced, an attacker (the miner, or any observer of a propagated block — no private key needed) can take a legitimate `miner_signature` over a block and compute its malleated twin, producing a second `NakamotoBlock` that:
- passes `check_miner_signature` (same recovered `Hash160` of the miner pubkey),
- is otherwise byte-identical (same transactions, same state root, same parent, same tenure),
- but has a *different* `signer_signature_hash` / `block_hash` / `block_id`.

Every signer-side mechanism that is supposed to recognize "this is the same block/proposal" operates on that hash: block proposal dedup ("block already tracked? `block_lookup_by_reward_cycle`"), pre-commit/signature bookkeeping (`add_block_pre_commit`, `add_block_signature`, `store_and_process_block_signature`), and the `DuplicateBlockFound` reject-reason path all key off `signer_signature_hash`: [7](#0-6) [8](#0-7) 

None of that logic re-derives or compares the *content* of two proposals to notice they are semantically the same block; it only compares the (malleable) identity hash.

This is the direct structural analog of the CVE-2022-23539 bug class: the report is about accepting an unintended/insufficiently-restricted signature form that lets a verifier be fooled about identity/authenticity binding. Here, accepting a non-canonical (malleated) miner signature lets an attacker mint a second valid "identity" for content that should be singular, defeating equality checks the rest of the state machine depends on.

### Impact Explanation
This breaks the "one canonical identity per proposed block" equality invariant relied upon throughout the signer protocol:
- A single one-slot miner can present the network with two block objects, A and A′, that are the same block in every consensus-relevant respect but carry different `signer_signature_hash`/`block_id` values. By steering which variant reaches which signers first (simple network-level control any miner already has over gossip/StackerDB writes), the miner can split the honest signer set's signing weight between A and A′. Neither variant then reaches the 70% weight threshold even though, combined, the honest signers agreed on the (single, real) block content — a liveness wedge on an otherwise-signable, valid block.
- Because `DuplicateBlockFound`/`block_lookup_by_reward_cycle` dedup is hash-keyed, a malleated re-broadcast of an already fully-signed/accepted block can be treated by a signer as a brand-new, distinct proposal at a height/tenure it believes is already settled, re-entering validation/signing logic for what the signer already considers a resolved, identical block — undermining the "we already decided this height" bookkeeping the codebase relies on for reorg protection.

This matches the specified High-impact criterion: a signer can be wedged into never finalizing a valid block (weight-split liveness stall) because of a broken equivocation/identity guard.

### Likelihood Explanation
Likelihood is high for the mechanism itself (ECDSA signature malleability requires no computational effort and no private key — it is pure arithmetic on `(r, s, recid)`), and it is directly reachable by a single miner slot without any signer collusion, matching the "one-slot miner (plus gossip)" threat model. What is *not* independently confirmed in this review is the exact severity of the downstream consequence in all code paths (e.g., whether `check_latest_block_in_tenure`'s tenure/height-based freshness check would, in practice, catch and reject a same-height malleated re-proposal for *every* timing window before a signer commits to either variant). The core structural flaw — identity hash committing to a malleable field, verified without canonicalization — is confirmed by direct code citation; the full blast radius across all signer-flow states was not exhaustively traced in this pass due to the volume of `signer.rs`/`signerdb.rs` state-transition logic.

### Recommendation
- Enforce low-S (canonical) signatures for the miner signature the same way it is already enforced for `verify()`/regular transaction signatures (`Secp256k1PublicKey::verify`), i.e. stop using `recover_to_pubkey_without_validating_low_s` for `recover_miner_pk`/`check_miner_signature`, or explicitly canonicalize `miner_signature` before it is folded into `signer_signature_hash_inner`.
- More robustly, stop including the raw signature bytes in the block's identity hash; commit instead to a canonicalized/normalized representation of the miner's signature (or exclude it from the identity hash the way `signer_signature` already is), so that `block_id`/`signer_signature_hash` are a pure function of consensus-relevant content and cannot be multiplied via signature malleability.
- Audit all `recover_to_pubkey_without_validating_low_s` call sites (`stackslib/src/chainstate/nakamoto/mod.rs`, `stacks-signer/src/v0/signer.rs`) to determine whether any of them feed into a value used as a dedup/identity key, and apply the same fix there.

### Proof of Concept
1. Miner produces a valid Nakamoto block `A` with header fields `(version, chain_length, burn_spent, consensus_hash, parent_block_id, tx_merkle_root, state_index_root, timestamp, pox_treatment, problematic_txs)` and signs it: `miner_signature = sign(miner_signature_hash(A))` — a standard low-S ECDSA signature `(r, s, recid)`.
2. Anyone (no private key required) computes the malleated signature `(r, n-s, recid')` for the same message; this is a valid publicly-computable transform recognized by `libsecp256k1`/`Secp256k1Signature::normalize_s` semantics.
3. Construct `A'` = identical header to `A` except `miner_signature = (r, n-s, recid')`.
4. Call `A.header.check_miner_signature(&miner_pkh)` and `A'.header.check_miner_signature(&miner_pkh)` — both succeed, because `recover_miner_pk` → `recover_to_pubkey_without_validating_low_s` accepts both forms and recovers the identical public key/hash160.
5. Observe `A.header.signer_signature_hash() != A'.header.signer_signature_hash()` (and thus `A.block_id() != A'.block_id()`), even though the two blocks carry identical consensus content — confirming that `signer_signature_hash`/`block_id` is not a faithful, collision-resistant identity for block content, and that the miner (or any relay) can mint a second valid identity for the same block purely from public data.

### Citations

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1026-1045)
```rust
    /// Inner calculation of the message digest for stackers to sign.
    /// This includes all fields _except_ the stacker signature.
    fn signer_signature_hash_inner(&self) -> Result<Sha512Trunc256Sum, CodecError> {
        let mut hasher = Sha512_256::new();
        let fd = &mut hasher;
        write_next(fd, &self.version)?;
        write_next(fd, &self.chain_length)?;
        write_next(fd, &self.burn_spent)?;
        write_next(fd, &self.consensus_hash)?;
        write_next(fd, &self.parent_block_id)?;
        write_next(fd, &self.tx_merkle_root)?;
        write_next(fd, &self.state_index_root)?;
        write_next(fd, &self.timestamp)?;
        write_next(fd, &self.miner_signature)?;
        write_next(fd, &self.pox_treatment)?;
        if Self::version_includes_problematic_txs(self.version) {
            write_next(fd, &self.problematic_txs)?;
        }
        Ok(Sha512Trunc256Sum::from_hasher(hasher))
    }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1047-1056)
```rust
    pub fn recover_miner_pk(&self) -> Option<StacksPublicKey> {
        let signed_hash = self.miner_signature_hash();
        let recovered_pk = StacksPublicKey::recover_to_pubkey_without_validating_low_s(
            signed_hash.bits(),
            &self.miner_signature,
        )
        .ok()?;

        Some(recovered_pk)
    }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1058-1069)
```rust
    pub fn block_hash(&self) -> BlockHeaderHash {
        // same as sighash -- we don't commit to signatures
        BlockHeaderHash(
            self.signer_signature_hash_inner()
                .expect("BUG: failed to serialize block header hash struct")
                .0,
        )
    }

    pub fn block_id(&self) -> StacksBlockId {
        StacksBlockId::new(&self.consensus_hash, &self.block_hash())
    }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1133-1143)
```rust
        for signature in self.signer_signature.iter() {
            let public_key = Secp256k1PublicKey::recover_to_pubkey_without_validating_low_s(
                message.bits(),
                signature,
            )
            .map_err(|_| {
                ChainstateError::InvalidStacksBlock(format!(
                    "Unable to recover public key from signature {}",
                    signature.to_hex()
                ))
            })?;
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1734-1758)
```rust
    /// Verify the miner signature over this block.
    /// If this is a shadow block, then this is always Ok(())
    pub(crate) fn check_miner_signature(
        &self,
        miner_pubkey_hash160: &Hash160,
    ) -> Result<(), ChainstateError> {
        if self.is_shadow_block() {
            return Ok(());
        }

        let recovered_miner_hash160 = self.recover_miner_pubkh()?;
        if &recovered_miner_hash160 != miner_pubkey_hash160 {
            warn!(
                "Nakamoto Stacks block signature mismatch: {recovered_miner_hash160} != {miner_pubkey_hash160} from leader-key";
                "consensus_hash" => %self.header.consensus_hash,
                "stacks_block_hash" => %self.header.block_hash(),
                "stacks_block_id" => %self.header.block_id()
            );
            return Err(ChainstateError::InvalidStacksBlock(
                "Invalid miner signature".into(),
            ));
        }

        Ok(())
    }
```

**File:** stacks-common/src/util/secp256k1/native.rs (L196-239)
```rust

    /// Recover message and signature to public key (will be compressed), while
    /// skipping validation that the signature is normalized to low-S. You shouldn't
    /// use this in new code.
    pub fn recover_to_pubkey_without_validating_low_s(
        msg: &[u8],
        sig: &MessageSignature,
    ) -> Result<Secp256k1PublicKey, &'static str> {
        Self::recover_to_pubkey_possibly_with_low_s_verification(msg, sig, false)
    }

    fn recover_to_pubkey_possibly_with_low_s_verification(
        msg: &[u8],
        sig: &MessageSignature,
        verify_low_s: bool,
    ) -> Result<Secp256k1PublicKey, &'static str> {
        _secp256k1.with(|ctx| {
            let msg = LibSecp256k1Message::from_slice(msg).map_err(|_e| {
                "Invalid message: failed to decode data hash: must be a 32-byte hash"
            })?;

            let secp256k1_sig = sig
                .to_secp256k1_recoverable()
                .ok_or("Invalid signature: failed to decode recoverable signature")?;

            if verify_low_s {
                let secp256k1_sig_standard = secp256k1_sig.to_standard();
                let mut secp256k1_sig_low_s = secp256k1_sig_standard;
                secp256k1_sig_low_s.normalize_s();
                if secp256k1_sig_low_s != secp256k1_sig_standard {
                    return Err("Invalid signature: high-S");
                }
            }

            let recovered_pubkey = ctx
                .recover_ecdsa(&msg, &secp256k1_sig)
                .map_err(|_e| "Invalid signature: failed to recover public key")?;

            Ok(Secp256k1PublicKey {
                key: recovered_pubkey,
                compressed: true,
            })
        })
    }
```

**File:** stacks-signer/src/v0/signer.rs (L1250-1281)
```rust
    /// Handle pre-commit message from another signer
    fn handle_block_pre_commit(
        &mut self,
        stacks_client: &StacksClient,
        sortition_state: &mut Option<SortitionsView>,
        stacker_address: &StacksAddress,
        block_hash: &Sha512Trunc256Sum,
    ) {
        let Some(mut block_info) = self.block_lookup_by_reward_cycle(block_hash) else {
            // A pre-commit for a block we have not seen proposed yet means the proposal
            // has not reached us. Log it at INFO: it is a direct signal that our view of
            // the proposal stream is behind the rest of the signer set.
            info!("{self}: Received block pre-commit for an unknown block, storing as pending";
                "signer_address" => %stacker_address,
                "signer_signature_hash" => %block_hash,
                "signer_weight" => self.signer_weights.get(stacker_address).copied().unwrap_or(0),
            );
            if let Err(e) = self
                .signer_db
                .add_pending_block_pre_commit_response(block_hash, stacker_address)
            {
                warn!("{self}: Failed to save pending block pre-commit response: {e:?}");
            }
            return;
        };
        // Always save the pre-commit - we will need to store signer responses for determining which
        // are misbehaving, offline, etc.
        // commit message is from a valid sender! store it
        self.signer_db
            .add_block_pre_commit(block_hash, stacker_address)
            .unwrap_or_else(|_| panic!("{self}: Failed to save block pre-commit"));

```

**File:** stacks-signer/src/v0/signer.rs (L2442-2466)
```rust
    /// Store the block acceptance signature and check if we have reached a consensus decision on the block because of it. If we have, update the block state accordingly and broadcast the block if accepted.
    fn store_and_process_block_signature(
        &mut self,
        stacks_client: &StacksClient,
        sortition_state: &mut Option<SortitionsView>,
        block_info: &mut BlockInfo,
        signer_address: &StacksAddress,
        signature: &MessageSignature,
    ) {
        let block_hash = &block_info.signer_signature_hash();
        // signature is valid! store it.
        // if this returns false, it means the signature already exists in the DB, so just return.
        if !self
            .signer_db
            .add_block_signature(block_hash, signer_address, signature)
            .unwrap_or_else(|_| panic!("{self}: Failed to save block signature"))
        {
            return;
        }

        // If this isn't our own signature and we haven't seen a pre-commit from this signer yet, try treating it as a pre-commit in case the caller is running an outdated version
        if signer_address != &self.stacks_address && !self.signer_db.has_committed(block_hash, signer_address).inspect_err(|e| warn!("Failed to check if pre-commit message already considered for {signer_address:?} for {block_hash}: {e}")).unwrap_or(false) {
            self.handle_block_pre_commit(stacks_client, sortition_state, signer_address, block_hash);
            return;
        }
```
