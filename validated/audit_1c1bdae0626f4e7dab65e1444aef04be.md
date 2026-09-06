No vulnerability found for this question.

**Reasoning:** `inner_consensus_serialize` in `libsigner/src/v0/messages.rs` is a pure wire-format serialization helper used by `BlockResponseData`, `BlockProposalData`, and similar structs. It only writes struct fields (e.g., `tenure_extend_timestamp`, `reject_reason`, `server_version`) into a byte buffer for backward-compatible versioned encoding — it does not read, write, store, or overwrite any signer decision state, signature slot, or acceptance/rejection tally. [1](#0-0) [2](#0-1) 

The actual accept/reject counting and "last decision" invariant logic lives in `stacks-signer/src/v0/signer.rs` (`handle_block_response`, `handle_block_rejection`, `store_and_process_block_rejection`, `determine_response`) and `stacks-signer/src/signerdb.rs` (`add_block_signature`, `add_block_rejection_signer_addr`, `get_block_rejection_signer_addrs`), not in `inner_consensus_serialize`. [3](#0-2) [4](#0-3) [5](#0-4) 

The existing test `reject_then_accept` in `signerdb.rs` demonstrates the DB layer's rejection-then-signature overwrite behavior explicitly and by design (a later signature clears prior rejection records for a given block_id, which is expected local-decision-tracking behavior for the same block, not a "stale accept ignoring a later reject" flaw), and it has no relation whatsoever to `inner_consensus_serialize`. [5](#0-4) 

The question's premise — that `inner_consensus_serialize` is where "signature slot overwritten so a later rejection is ignored" occurs — does not match the actual code at that location. There is no reachable path from a BlockProposal or gossiped signer message that exercises `inner_consensus_serialize` in a way that affects accept/reject tallying or the "final response == last decision" invariant, since that function performs no state mutation or decision logic at all.

### Citations

**File:** libsigner/src/v0/messages.rs (L286-292)
```rust
/// Work around for the fact that a lot of the structs being deserialized are not defined in messages.rs
pub trait StacksMessageCodecExtensions: Sized {
    /// Serialize the struct to the provided writer
    fn inner_consensus_serialize<W: Write>(&self, fd: &mut W) -> Result<(), CodecError>;
    /// Deserialize the struct from the provided reader
    fn inner_consensus_deserialize<R: Read>(fd: &mut R) -> Result<Self, CodecError>;
}
```

**File:** libsigner/src/v0/messages.rs (L1602-1614)
```rust
impl StacksMessageCodec for BlockResponseData {
    /// Serialize the block response data.
    /// When creating a new version of the block response data, we are only ever
    /// appending new bytes to the end of the struct. When serializing, we use
    /// `bytes_len` to ensure that older versions of the code can read through the
    /// end of the serialized bytes.
    fn consensus_serialize<W: Write>(&self, fd: &mut W) -> Result<(), CodecError> {
        write_next(fd, &self.version)?;
        let mut inner_bytes = vec![];
        self.inner_consensus_serialize(&mut inner_bytes)?;
        write_next(fd, &inner_bytes)?;
        Ok(())
    }
```

**File:** stacks-signer/src/v0/signer.rs (L1782-1797)
```rust
    /// Handle block response messages from a signer
    fn handle_block_response(
        &mut self,
        stacks_client: &StacksClient,
        block_response: &BlockResponse,
        sortition_state: &mut Option<SortitionsView>,
    ) {
        match block_response {
            BlockResponse::Accepted(accepted) => {
                self.handle_block_signature(stacks_client, sortition_state, accepted);
            }
            BlockResponse::Rejected(block_rejection) => {
                self.handle_block_rejection(block_rejection, sortition_state);
            }
        };
    }
```

**File:** stacks-signer/src/v0/signer.rs (L2208-2265)
```rust
    /// Handle an observed rejection from another signer
    fn handle_block_rejection(
        &mut self,
        rejection: &BlockRejection,
        sortition_state: &mut Option<SortitionsView>,
    ) {
        debug!("{self}: Received a block-reject signature: {rejection:?}");

        let block_hash = &rejection.signer_signature_hash;
        let signature = &rejection.signature;

        // recover public key
        let Ok(public_key) = rejection.recover_public_key() else {
            debug!("{self}: Received block rejection with an unrecovarable signature. Will not store.";
               "signer_signature_hash" => %block_hash,
               "signature" => %signature
            );
            return;
        };

        // authenticate the signature -- it must be signed by one of the stacking set
        let signer_address = StacksAddress::p2pkh(self.mainnet, &public_key);
        if !self.is_valid_signer(&signer_address) {
            debug!("{self}: Received block rejection with an invalid signature. Will not store.";
                "signer_public_key" => ?public_key,
                "signer_address" => %signer_address,
                "signer_signature_hash" => %block_hash,
                "signature" => %signature
            );
            return;
        }

        let Some(mut block_info) = self.block_lookup_by_reward_cycle(block_hash) else {
            if let Err(e) = self.signer_db.add_pending_block_rejection_response(
                block_hash,
                &signer_address,
                (&rejection.response_data.reject_reason).into(),
            ) {
                warn!("{self}: Failed to add pending block rejection response: {e:?}");
            }
            return;
        };

        info!("{self}: Received block rejection";
            "signer_pubkey" => public_key.to_hex(),
            "signer_signature_hash" => %block_hash,
            "consensus_hash" => %block_info.block.header.consensus_hash,
            "block_height" => block_info.block.header.chain_length,
            "reject_reason" => ?rejection.response_data.reject_reason,
        );

        self.store_and_process_block_rejection(
            sortition_state,
            &mut block_info,
            &signer_address,
            (&rejection.response_data.reject_reason).into(),
        );
    }
```

**File:** stacks-signer/src/signerdb.rs (L3234-3264)
```rust
    #[test]
    fn reject_then_accept() {
        let db_path = tmp_db_path();
        let db = SignerDb::new(db_path).expect("Failed to create signer db");

        let block_id = Sha512Trunc256Sum::from_data("foo".as_bytes());
        let address = StacksAddress::burn_address(false);
        let sig1 = MessageSignature([0x11; 65]);

        assert_eq!(db.get_block_signatures(&block_id).unwrap(), vec![]);

        assert!(db
            .add_block_rejection_signer_addr(
                &block_id,
                &address,
                RejectReasonPrefix::InvalidParentBlock
            )
            .unwrap());
        assert_eq!(
            db.get_block_rejection_signer_addrs(&block_id).unwrap(),
            vec![(address.clone(), RejectReasonPrefix::InvalidParentBlock)]
        );

        assert!(db.add_block_signature(&block_id, &address, &sig1).unwrap());
        assert_eq!(db.get_block_signatures(&block_id).unwrap(), vec![sig1]);
        assert!(db
            .get_block_rejection_signer_addrs(&block_id)
            .unwrap()
            .is_empty());
    }

```
