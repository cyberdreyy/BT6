### Title
Host preimage `KeyValueStore` silently allows overwriting already-registered, hash-verified preimage content — (File: `crates/proof/host/src/kv/mem.rs`)

### Summary
The CVE-2024-27297 bug class is: content is verified/hash-checked once and then registered as immutable, but a side channel later mutates the underlying bytes without re-validation, so downstream consumers unknowingly use corrupted content. The same pattern exists in the fault-proof host's preimage `KeyValueStore`: preimages are hash-checked (when checkable) only at the moment they are first `set()`, but `set()` itself performs no "does this key already hold a different, previously-validated value" check and unconditionally overwrites. The `OracleServer` that later serves these bytes to the untrusted client program (`crates/proof/preimage/src/oracle.rs`) also performs no re-verification at serve time — it just forwards whatever is currently stored.

### Finding Description
`MemoryKeyValueStore::set` and `DiskKeyValueStore::set` unconditionally overwrite the value at a key with no check against a prior value: [1](#0-0) [2](#0-1) 
`SplitKeyValueStore::set` forwards unconditionally as well: [3](#0-2) 

Contrast this with the zkVM's in-guest preimage store, which explicitly implements the missing invariant and rejects an overwrite with different bytes: [4](#0-3) 

Several `PreimageKeyType`s are documented as *not* hash-verifiable from the value alone and are explicitly deferred to a later "derivation" check that does not occur in the host's own `KeyValueStore`: [5](#0-4) 

The host writes such non-self-verifying preimages (e.g. `Blob`, keyed by `keccak256(commitment ++ root-of-unity-index)`, not by the value itself) directly via unconditional `kv_lock.set()` calls across multiple hint round-trips: [6](#0-5) 

Finally, when the host serves preimages back to the client program over the oracle channel, it performs no hash re-check of the value it is returning — it simply forwards whatever `fetcher.get_preimage(key)` currently returns: [7](#0-6) 

So for hash-checkable key types (`Keccak256`/`Sha256`), verification happens only once, at the specific `set()` call site that happens to include a check (e.g. `HintType::L2StateNode`), not inside the store itself: [8](#0-7) 
Many other call sites write into the *same shared store* without any such check (e.g. `insert_execution_witness_preimages`, `insert_l1_header_preimage`, `L2AccountProof`), so the guarantee that "a key's content, once verified, can never change" depends entirely on every call site behaving correctly and never race/duplicate-writing to the same key — a guarantee the store itself does not enforce, unlike the zkVM `PreimageStore` which does enforce it centrally.

### Impact Explanation
If any code path (prefetch concurrency, a duplicate/racing hint, an untrusted or misbehaving upstream L1/L2 RPC provider the host trusts, or a future call site that forgets the ad-hoc hash check) writes a second, different value under a key that was already registered and validated, the store silently accepts the corruption. The client program (the fault-proof execution engine that later derives the claimed output root from these preimages) has no independent way to detect this, because the `OracleServer` never re-verifies content against the key at serve time. This can lead to the fault-proof program deriving and asserting a **wrong output root**, undermining the correctness guarantee the dispute-game system relies on for finality — a "wrong provable output root" impact.

### Likelihood Explanation
Exploitability depends on finding a concrete way to trigger a second, differing `set()` on an already-populated key (e.g., via a malicious/compromised upstream L1 or L2 RPC data source that the host process consumes, exploiting the batched/concurrent prefetch paths (`insert_execution_witness_preimages_batched`, `PayloadWitnessPrefetcher`) racing with foreground hint handling, or via non-self-verifying key types such as `Blob`/`Precompile` where no per-write hash check exists at all). This is architecturally analogous to the Nix bug (validate-then-mutate via a side channel) but requires either a compromised/adversarial data provider feeding the host or a race condition between prefetch and on-demand hint paths — I could not fully trace every concurrent call site within the available context to confirm a fully deterministic single-request trigger, so likelihood should be treated as **moderate, not proven end-to-end**.

### Recommendation
Add the same "reject overwrite with a different value" invariant already implemented in `PreimageStore::save_preimage` (`crates/proof/zk/utils/src/witness/preimage_store.rs`) to `KeyValueStore::set` for `MemoryKeyValueStore`, `DiskKeyValueStore`, and `SplitKeyValueStore`, and additionally have `OracleServer::next_preimage_request` re-verify hash-derivable key types against the value before serving it, closing the gap for non-hash-derivable types (`Blob`, `Precompile`) by validating them against their composite scheme at store-time rather than deferring to "derivation."

### Proof of Concept
Conceptual repro (not exercised end-to-end due to tool limits):
1. Host handles `HintType::L2StateNode` for hash `H`, fetches correct preimage `P1`, verifies `keccak256(P1) == H`, and stores `(H, P1)` via `MemoryKeyValueStore::set`.
2. A racing/duplicate write path (e.g. `insert_execution_witness_preimages_batched`, prefetch, or a compromised upstream RPC response reused for the same `PreimageKey`) calls `set(H, P2)` with `P2 != P1` without any check against the existing entry — `MemoryKeyValueStore::set`/`DiskKeyValueStore::set` accept it unconditionally (`crates/proof/host/src/kv/mem.rs:30-33`, `crates/proof/host/src/kv/disk.rs:42-44`).
3. `OracleServer::next_preimage_request` later serves `P2` to the client program with no re-check (`crates/proof/preimage/src/oracle.rs:115-136`), causing the fault-proof program to compute state/output roots over corrupted data.

### Citations

**File:** crates/proof/host/src/kv/mem.rs (L30-33)
```rust
    fn set(&mut self, key: B256, value: Vec<u8>) -> Result<()> {
        self.store.insert(key, value);
        Ok(())
    }
```

**File:** crates/proof/host/src/kv/disk.rs (L42-44)
```rust
    fn set(&mut self, key: B256, value: Vec<u8>) -> Result<()> {
        self.db.put(*key, value).map_err(|e| HostError::KeyValueSetFailed(e.to_string()))
    }
```

**File:** crates/proof/host/src/kv/split.rs (L45-47)
```rust
    fn set(&mut self, key: B256, value: Vec<u8>) -> Result<()> {
        self.remote_store.set(key, value)
    }
```

**File:** crates/proof/zk/utils/src/witness/preimage_store.rs (L60-76)
```rust
    /// Insert a preimage, rejecting overwrites with different values.
    pub fn save_preimage(&mut self, key: PreimageKey, value: Vec<u8>) -> PreimageOracleResult<()> {
        check_preimage(&key, &value)?;

        match self.preimage_map.entry(key) {
            Entry::Vacant(e) => {
                e.insert(value);
            }
            Entry::Occupied(e) => {
                if e.get() != &value {
                    return Err(PreimageOracleError::Other("cannot overwrite key".to_string()));
                }
            }
        };

        Ok(())
    }
```

**File:** crates/proof/tee/nitro-enclave/src/oracle.rs (L47-69)
```rust
    /// Verify that a preimage's content matches its key for hash-based key types.
    ///
    /// For [`PreimageKeyType::Keccak256`] keys the keccak256 digest of `value`
    /// must produce a key equal to `key`. For [`PreimageKeyType::Sha256`] keys
    /// the SHA-256 digest is checked instead. Local and `GlobalGeneric` keys are
    /// context-dependent and cannot be verified by hash, so they are accepted
    /// without validation.
    ///
    /// Blob and Precompile keys use composite hashing schemes that cannot be
    /// validated with the value alone, so they are also accepted as-is.
    fn check_preimage(key: &PreimageKey, value: &[u8]) -> crate::Result<()> {
        let expected_hash: Option<[u8; 32]> = match key.key_type() {
            PreimageKeyType::Keccak256 => Some(keccak256(value).0),
            PreimageKeyType::Sha256 => Some(sha2::Sha256::digest(value).into()),
            // Blob keys are `keccak256(commitment ++ z)` and precompile keys are
            // `keccak256(address ++ input)` — neither can be re-derived from the
            // stored value alone, so we skip verifying them here and instead verify them
            // during derivation.
            PreimageKeyType::Local
            | PreimageKeyType::GlobalGeneric
            | PreimageKeyType::Blob
            | PreimageKeyType::Precompile => None,
        };
```

**File:** crates/proof/host/src/handler.rs (L1013-1042)
```rust
            let mut kv_lock = kv.write().await;

            kv_lock.set(
                PreimageKey::new(*hash, PreimageKeyType::Sha256).into(),
                commitment.to_vec(),
            )?;

            let mut blob_key = [0u8; 80];
            blob_key[..48].copy_from_slice(commitment.as_ref());
            for i in 0..FIELD_ELEMENTS_PER_BLOB {
                blob_key[48..].copy_from_slice(
                    ROOTS_OF_UNITY[i as usize].into_bigint().to_bytes_be().as_ref(),
                );
                let blob_key_hash = keccak256(blob_key.as_ref());

                kv_lock.set(PreimageKey::new_keccak256(*blob_key_hash).into(), blob_key.into())?;
                kv_lock.set(
                    PreimageKey::new(*blob_key_hash, PreimageKeyType::Blob).into(),
                    blob.as_ref()[(i as usize) << 5..(i as usize + 1) << 5].to_vec(),
                )?;
            }

            blob_key[72..].copy_from_slice(FIELD_ELEMENTS_PER_BLOB.to_be_bytes().as_ref());
            let blob_key_hash = keccak256(blob_key.as_ref());

            kv_lock.set(PreimageKey::new_keccak256(*blob_key_hash).into(), blob_key.into())?;
            kv_lock.set(
                PreimageKey::new(*blob_key_hash, PreimageKeyType::Blob).into(),
                proof.to_vec(),
            )?;
```

**File:** crates/proof/host/src/handler.rs (L1169-1189)
```rust
        HintType::L2StateNode => {
            if hint.data.len() != 32 {
                return Err(HostError::InvalidHintDataLength);
            }

            let hash: B256 = hint.data.as_ref().try_into()?;

            error!(node_hash = %hash, "debug_executePayload failed to return a complete witness");

            let preimage: Bytes = providers.l2.client().request("debug_dbGet", &[hash]).await?;
            let actual_hash = keccak256(preimage.as_ref());
            if actual_hash != hash {
                return Err(HostError::StateNodePreimageHashMismatch {
                    expected: hash,
                    actual: actual_hash,
                });
            }

            let mut kv_write_lock = kv.write().await;
            kv_write_lock.set(PreimageKey::new_keccak256(*hash).into(), preimage.into())?;
        }
```

**File:** crates/proof/preimage/src/oracle.rs (L115-136)
```rust
    async fn next_preimage_request<F>(&self, fetcher: &F) -> Result<(), PreimageOracleError>
    where
        F: PreimageFetcher + Send + Sync,
    {
        // Read the preimage request from the client, and throw early if there isn't any.
        let mut buf = [0u8; 32];
        self.channel.read_exact(&mut buf).await?;
        let preimage_key = PreimageKey::try_from(buf)?;

        trace!(target: "oracle_server", key = %preimage_key, "Fetching preimage");

        // Fetch the preimage value from the preimage getter.
        let value = fetcher.get_preimage(preimage_key).await?;

        // Write the length as a big-endian u64 followed by the data.
        self.channel.write(value.len().to_be_bytes().as_ref()).await?;
        self.channel.write(value.as_ref()).await?;

        trace!(target: "oracle_server", key = %preimage_key, "Successfully wrote preimage data");

        Ok(())
    }
```
