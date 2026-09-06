### Title
Signer's block-signature preimage (`signer_signature_hash`) omits any chain/network identifier, making signer signatures cross-context replayable - (File: `stackslib/src/chainstate/nakamoto/mod.rs`)

### Summary
The Overprotocol report flags reused/undifferentiated `ChainID` values and shared bootnode config as a cross-pollination/replay risk between networks that should otherwise be cryptographically distinct. The direct analog in this repo is that the actual bytes a signer signs when approving a Nakamoto block — `NakamotoBlockHeader::signer_signature_hash()` — never commits to a chain id, network id, or mainnet/testnet flag. The only place `chain_id` is checked is on the node's HTTP validation endpoint request (`postblock_proposal.rs`), not in the data that is cryptographically signed, and not in `stacks-signer`'s own `check_proposal` logic before a signer signs.

### Finding Description
`signer_signature_hash_inner` in `stackslib/src/chainstate/nakamoto/mod.rs` builds the preimage a signer key signs from: `version`, `chain_length`, `burn_spent`, `consensus_hash`, `parent_block_id`, `tx_merkle_root`, `state_index_root`, `timestamp`, `miner_signature`, `pox_treatment`, and (epoch-gated) `problematic_txs`. [1](#0-0) 
There is no `chain_id`/network identifier anywhere in this preimage, nor in `miner_signature_hash_inner`. [2](#0-1) 

The final consensus-critical verification, `verify_signer_signatures`, likewise only recovers the pubkey against `self.signer_signature_hash()` and checks it against the `reward_set` — again no chain/network binding is checked at this stage: [3](#0-2) 

On the signer side, `stacks-signer`'s own gating logic (`check_proposal` in `chainstate/v1.rs`) validates consensus hash, miner pkh, and bitvec, but does not validate the block against any chain-id/network-id binding before the signer produces its `MessageSignature` over `signer_signature_hash`: [4](#0-3) 

The only `chain_id` check that exists anywhere near block signing is on the *node's* `/v3/block_proposal` HTTP validation endpoint (`postblock_proposal.rs`), which rejects a request whose declared `chain_id` doesn't match the node's own configured chain id (`NetworkChainMismatch`) — this is a request-context sanity check on the node's local RPC call, not a value that is folded into the signed hash or checked by `verify_signer_signatures` when the block is later accepted into a chainstate: [5](#0-4) 

Because `signer_signature_hash` is chain/network-agnostic, and `BlockRejection`'s explicit `chain_id` field is used only for *rejection* message signing (not the actual block-approval signature that ends up in `NakamotoBlockHeader.signer_signature`): [6](#0-5) 
a `MessageSignature` a signer produces over one block header is a function purely of header fields that carry no chain/network domain separator. If two Stacks-protocol network instances (e.g., mainnet vs. a fork/testnet/appchain) share an overlapping reward set (same signer keys, which — exactly as in the Overprotocol report — is a realistic operational scenario when node operators reuse keys or when a chain forks and inherits the prior reward set), a signature legitimately collected for a block header with a given `(version, chain_length, burn_spent, consensus_hash, parent_block_id, tx_merkle_root, state_index_root, timestamp, miner_signature, pox_treatment, problematic_txs)` tuple on one network is byte-for-byte a valid signature for an identical-tuple header on the other network, since `verify_signer_signatures` has no way to reject it as out-of-context.

### Impact Explanation
This is a cross-context-valid signature: a signer's cryptographic approval, produced under one chain/network context, is indistinguishable from — and therefore fully valid for — the same block header replayed in a different chain/network context. That satisfies the Critical bar in the rules ("a cross-context-valid signature"). It undermines the assumption that a signer's signature is scoped to the network it believes it is signing for, and is directly the same root-cause class as the reported bug: absence of a unique/binding chain identifier in a commitment shared across network instances.

### Likelihood Explanation
Triggering this requires only: (a) a single signer (no majority needed) to sign a block on network A, and (b) a header with the identical unsigned-field tuple existing/being constructed on network B whose reward set includes that signer's key. Reward-set key reuse across mainnet/testnet, or across a hard-forked chain that inherits the same PoX-4/PoX-5 stacked signer set, is a realistic and previously-seen operational pattern in Stacks deployments, making this a plausible, low-effort scenario rather than a purely theoretical one.

### Recommendation
Bind `signer_signature_hash` (and `miner_signature_hash`) to a chain/network domain separator (e.g., the burnchain `chain_id`/mainnet flag) so that signatures produced under one network context cannot be replayed as valid under another, and have `verify_signer_signatures` check that binding at block-acceptance time — not merely as an RPC-request-level check in `postblock_proposal.rs`.

### Proof of Concept
1. Two Stacks-protocol network instances (A and B) share an overlapping reward set (e.g., B is a testnet/fork of A that inherited the same PoX signer keys).
2. A miner (or attacker with mempool access) constructs a `NakamotoBlockHeader` on network B whose fields `(version, chain_length, burn_spent, consensus_hash, parent_block_id, tx_merkle_root, state_index_root, timestamp, miner_signature, pox_treatment, problematic_txs)` are made identical to a header that a signer `S` (present in both A's and B's reward set) previously signed on network A — this is feasible because none of these fields commit to a chain id.
3. Compute `signer_signature_hash()` per `stackslib/src/chainstate/nakamoto/mod.rs:1026-1045` — it is identical to the hash signed on network A.
4. Reuse the `MessageSignature` collected for that hash on network A as `signer_signature` for the network-B block.
5. `verify_signer_signatures` (`nakamoto/mod.rs:1096-1136`) recovers `S`'s pubkey from the reused signature and accepts it as a valid approval toward network B's threshold, without ever having asked signer `S` to approve anything on network B.

### Citations

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1006-1024)
```rust
    /// Inner calculation of the message digest for miners to sign.
    /// This includes all fields _except_ the signatures.
    fn miner_signature_hash_inner(&self) -> Result<Sha512Trunc256Sum, CodecError> {
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
        write_next(fd, &self.pox_treatment)?;
        if Self::version_includes_problematic_txs(self.version) {
            write_next(fd, &self.problematic_txs)?;
        }
        Ok(Sha512Trunc256Sum::from_hasher(hasher))
    }
```

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

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1096-1136)
```rust
    #[cfg_attr(test, mutants::skip)]
    pub fn verify_signer_signatures(
        &self,
        reward_set: &RewardSet,
        epoch_id: StacksEpochId,
    ) -> Result<u32, ChainstateError> {
        let message = self.signer_signature_hash();
        let Some(signers) = reward_set.signers() else {
            return Err(ChainstateError::InvalidStacksBlock(
                "No signers in the reward set".to_string(),
            ));
        };

        // if this is a shadow block, then its signing weight is as if every signer signed it, even
        // though the signature vector is undefined.
        if self.is_shadow_block() {
            return Ok(self.get_shadow_signer_weight(reward_set)?);
        }

        let mut total_weight_signed: u32 = 0;
        // `last_index` is used to prevent out-of-order signatures
        let mut last_index = None;
        // Before Epoch 4.0, signature order check contained a bug, so gate the
        // strict ordering behavior on the epoch.
        let strict_order = epoch_id.enforces_strict_signature_order();

        let total_weight = reward_set
            .total_signing_weight()
            .map_err(|_| ChainstateError::NoRegisteredSigners(0))?;

        // HashMap of <PublicKey, (Signer, Index)>
        let mut signers_by_pk: HashMap<_, _> = signers
            .iter()
            .enumerate()
            .map(|(i, signer)| (&signer.signing_key, (signer, i)))
            .collect();

        for signature in self.signer_signature.iter() {
            let public_key = Secp256k1PublicKey::recover_to_pubkey_without_validating_low_s(
                message.bits(),
                signature,
```

**File:** stacks-signer/src/chainstate/v1.rs (L238-275)
```rust
        let miner_pkh = Hash160::from_data(&miner_pk.to_bytes_compressed());
        let Some(proposed_by) =
            (if block.header.consensus_hash == self.cur_sortition.data.consensus_hash {
                Some(ProposedBy::CurrentSortition(&self.cur_sortition))
            } else {
                None
            })
            .or_else(|| {
                self.last_sortition.as_ref().and_then(|last_sortition| {
                    if block.header.consensus_hash == last_sortition.data.consensus_hash {
                        Some(ProposedBy::LastSortition(last_sortition))
                    } else {
                        None
                    }
                })
            })
        else {
            if reset_view_if_wrong_consensus_hash {
                info!(
                    "Miner block proposal has consensus hash that is neither the current or last sortition. Resetting view.";
                    "proposed_block_consensus_hash" => %block.header.consensus_hash,
                    "current_sortition_consensus_hash" => ?self.cur_sortition.data.consensus_hash,
                    "last_sortition_consensus_hash" => ?self.last_sortition.as_ref().map(|x| &x.data.consensus_hash),
                );
                self.reset_view(client)
                    .map_err(SignerChainstateError::from)?;
                return self.check_proposal(client, signer_db, block, false, replay_set);
            }
            warn!(
                "Miner block proposal has consensus hash that is neither the current or last sortition. Considering invalid.";
                "proposed_block_consensus_hash" => %block.header.consensus_hash,
                "signer_signature_hash" => %block.header.signer_signature_hash(),
                "current_sortition_consensus_hash" => ?self.cur_sortition.data.consensus_hash,
                "last_sortition_consensus_hash" => ?self.last_sortition.as_ref().map(|x| &x.data.consensus_hash),
            );
            return Err(RejectReason::SortitionViewMismatch);
        };

```

**File:** stacks-node/src/tests/nakamoto_integrations.rs (L3562-3571)
```rust
        (
            "Invalid `chain_id`",
            {
                let mut p = proposal.clone();
                p.chain_id ^= 0xFFFFFFFF;
                sign(&p)
            },
            HTTP_ACCEPTED,
            Some(Err(ValidateRejectCode::NetworkChainMismatch)),
        ),
```

**File:** libsigner/src/v0/messages.rs (L1713-1730)
```rust
/// A rejection response from a signer for a proposed block
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct BlockRejection {
    /// The reason for the rejection
    pub reason: String,
    /// The reason code for the rejection
    pub reason_code: RejectCode,
    /// The signer signature hash of the block that was rejected
    pub signer_signature_hash: Sha512Trunc256Sum,
    /// The signer's signature across the rejection
    pub signature: MessageSignature,
    /// The chain id
    pub chain_id: u32,
    /// Signer message metadata
    pub metadata: SignerMessageMetadata,
    /// Extra versioned block response data
    pub response_data: BlockResponseData,
}
```
