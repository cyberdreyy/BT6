[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** stackslib/src/net/stackerdb/mod.rs (L679-697)
```rust
        // validate -- must be signed by the expected author
        let addr = match self
            .stackerdbs
            .get_slot_signer(smart_contract_id, data.slot_id)?
        {
            Some(addr) => addr,
            None => {
                return Ok(false);
            }
        };

        let slot_metadata = data.get_slot_metadata();
        if !slot_metadata.verify(&addr)? {
            info!(
                "StackerDBChunk for {} ID {} is not signed by {}",
                smart_contract_id, data.slot_id, &addr
            );
            return Ok(false);
        }
```

**File:** stackslib/src/net/tests/relay/nakamoto.rs (L1278-1303)
```rust
    // --- Test 3: Valid signature from the WRONG signer should be REJECTED ---
    let wrong_privk = StacksPrivateKey::from_seed(&[99]);

    let mut wrong_signer_chunk = StackerDBPushChunkData {
        contract_id: contract_id.clone(),
        rc_consensus_hash: future_consensus_hash,
        chunk_data: StackerDBChunkData::new(0, 1, vec![1, 2, 3, 4, 5]),
    };
    wrong_signer_chunk.chunk_data.sign(&wrong_privk).unwrap();

    let result = peer
        .network
        .handle_unsolicited_StackerDBPushChunk(
            &mut stacks_node.chainstate,
            1,
            &preamble,
            &wrong_signer_chunk,
            false,
        )
        .unwrap();

    assert_eq!(
        result,
        (false, false),
        "chunk signed by wrong signer must be rejected on FutureView path"
    );
```

**File:** stackslib/src/net/stackerdb/db.rs (L663-675)
```rust
    /// Get a versioned chunk out of this database.  If the version is not present, then None will
    /// be returned.
    pub fn get_chunk(
        &self,
        smart_contract: &QualifiedContractIdentifier,
        slot_id: u32,
        slot_version: u32,
    ) -> Result<Option<StackerDBChunkData>, net_error> {
        let stackerdb_id = self.get_stackerdb_id(smart_contract)?;
        let qry = "SELECT slot_id,version,signature,data FROM chunks WHERE stackerdb_id = ?1 AND slot_id = ?2 AND version = ?3";
        let args = params![stackerdb_id, slot_id, slot_version];
        query_row(&self.conn, qry, args).map_err(|e| e.into())
    }
```

**File:** stacks-signer/src/client/stackerdb.rs (L256-281)
```rust
    pub fn get_messages<T: SignerMessage<M>>(
        session: &mut StackerDBSession,
        slot_ids: &[u32],
    ) -> Result<Vec<T>, ClientError> {
        let mut messages = vec![];
        let send_request = || {
            session
                .get_latest_chunks(slot_ids)
                .map_err(backoff::Error::transient)
        };
        let chunk_ack = retry_with_exponential_backoff(send_request)?;
        for (i, chunk) in chunk_ack.iter().enumerate() {
            let Some(data) = chunk else {
                continue;
            };
            let Ok(message) = read_next::<T, _>(&mut &data[..]) else {
                if !data.is_empty() {
                    warn!("Failed to deserialize chunk data into a SignerMessage");
                    debug!("slot #{i}: Failed chunk ({}): {data:?}", &data.len(),);
                }
                continue;
            };
            messages.push(message);
        }
        Ok(messages)
    }
```
