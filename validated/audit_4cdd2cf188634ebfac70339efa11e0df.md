## Analog Found

### Title
Fault-proof host accepts unverified L2 contract bytecode preimages from a compromised/buggy L2 RPC - (File: crates/proof/host/src/handler.rs)

### Summary
The curl CVE describes a "fetch-by-hash, trust-without-verify" bug class: content retrieved by hash is used/kept even when it doesn't match the expected digest. The Base fault-proof host reproduces this exact pattern for L2 contract bytecode: every other hash-addressed preimage type fetched by the host recomputes the hash of the data it just fetched and rejects a mismatch, but the `HintType::L2Code` handler stores the RPC response directly under the requested hash without ever checking that `keccak256(code) == hash`.

### Finding Description
In `handle_hint_inner`, the `HintType::L1BlockHeader` branch fetches a raw header via `debug_getRawHeader` and explicitly re-derives the hash before accepting it: [1](#0-0) 

The `HintType::L2StateNode` branch does the same for MPT state nodes fetched via `debug_dbGet`: [2](#0-1) 

Both mismatches are surfaced as dedicated error variants that abort the operation rather than silently accepting mismatched content: [3](#0-2) 

In contrast, the `HintType::L2Code` branch — which fetches contract bytecode keyed by `code_hash` via `debug_dbGet` — stores the returned bytes verbatim under `PreimageKey::new_keccak256(*hash)` with no re-hash/compare step at all: [4](#0-3) 

The guest-side consumer, `OracleL2ChainProvider::bytecode_by_hash`, sends the `L2Code` hint and then reads the value directly out of the oracle by key, trusting that whatever was stored under that key is the correct bytecode for `hash`: [5](#0-4) 

This is the same defense-in-depth gap the CVE describes: the codebase's own comments acknowledge that the L1/L2 RPC endpoints queried by the host may be "compromised or buggy" (see the identical rationale used for the header-hash check in `crates/proof/challenge/src/validator.rs:170-172` and the header/state-node checks above), yet the L2 bytecode path omits the equivalent safeguard present everywhere else hash-addressed content is fetched from an untrusted/semi-trusted RPC.

### Impact Explanation
The fault-proof program (dispute-game participant path) uses `base-proof-host` to build the oracle witness that is later replayed inside the FPVM/zkVM to compute a claimed output root. If the L2 node the host queries (`providers.l2`) is compromised, buggy, or simply returns stale/incorrect bytecode for a `debug_dbGet(code_hash)` call, the host will store that incorrect bytecode under the `PreimageKey` for the *requested* hash without detecting the mismatch. During derivation/execution replay, the executor will load this incorrect bytecode as if it were the genuine contract code for that address, producing a wrong EVM execution trace and therefore a wrong computed output root. Depending on which side of a dispute controls or influences the L2 RPC data source used by the host, this can result in the fault-proof program computing and validating an incorrect output root (mirrors "wrong provable output root" impact), because there is no cryptographic tie between the code actually executed and the code hash committed on-chain via the account's `codeHash` field.

### Likelihood Explanation
The `L2Code` hint path is exercised on every dispute-game/fault-proof witness build that touches contract execution (essentially every game), since bytecode fetches are core to EVM execution. The precondition is a compromised or malfunctioning L2 RPC backing the host (the same threat model the codebase itself explicitly defends against for headers and state nodes), making this a realistic operational risk rather than a purely theoretical one — the surrounding code demonstrates the authors are already aware of, and defend against, this exact RPC-integrity threat for other preimage types, but missed it here.

### Recommendation
In the `HintType::L2Code` branch of `handle_hint_inner` (`crates/proof/host/src/handler.rs`), recompute `keccak256` over the fetched `code` bytes and compare it against the requested `hash` before writing to the KV store, returning a new `HostError::CodeHashPreimageMismatch { expected, actual }` (mirroring `StateNodePreimageHashMismatch` / `HeaderPreimageHashMismatch`) on failure, consistent with the pattern already used for `L1BlockHeader` and `L2StateNode`.

### Proof of Concept
1. Configure the fault-proof host (`base-proof-host`) to point at a malicious/compromised L2 RPC endpoint (or a buggy node with a stale/corrupted state DB).
2. During witness generation for a dispute game, the derivation/execution pipeline requests bytecode for some `code_hash` via the `L2Code` hint (`crates/proof/proof/src/l2/chain_provider.rs:193-208`).
3. The malicious RPC responds to `debug_dbGet([CODE_PREFIX || code_hash])` (or the fallback `debug_dbGet([code_hash])`) with attacker-chosen bytes instead of the genuine bytecode.
4. `handle_hint_inner`'s `HintType::L2Code` branch (`crates/proof/host/src/handler.rs:1140-1167`) stores these attacker-chosen bytes directly under `PreimageKey::new_keccak256(*hash)` — no check is performed, unlike the adjacent `L2StateNode`/`L1BlockHeader` branches.
5. When the guest replays execution using this witness, `bytecode_by_hash` returns the tampered bytecode for the affected contract, causing execution to diverge from the true on-chain semantics and yielding an incorrect output root/claim for the dispute game.

### Citations

**File:** crates/proof/host/src/handler.rs (L949-956)
```rust
            let header = Header::decode(&mut raw_header.as_ref())?;
            let decoded_hash = header.hash_slow();
            if decoded_hash != hash {
                return Err(HostError::HeaderPreimageHashMismatch {
                    expected: hash,
                    actual: decoded_hash,
                });
            }
```

**File:** crates/proof/host/src/handler.rs (L1140-1167)
```rust
        HintType::L2Code => {
            const CODE_PREFIX: u8 = b'c';

            if hint.data.len() != 32 {
                return Err(HostError::InvalidHintDataLength);
            }

            let hash: B256 = hint.data.as_ref().try_into()?;

            let code_key = [&[CODE_PREFIX], hash.as_slice()].concat();
            let code = providers
                .l2
                .client()
                .request::<&[Bytes; 1], Bytes>("debug_dbGet", &[code_key.into()])
                .await;

            let code = match code {
                Ok(code) => code,
                Err(_) => providers
                    .l2
                    .client()
                    .request::<&[B256; 1], Bytes>("debug_dbGet", &[hash])
                    .await
                    .map_err(|e| HostError::CodeHashPreimageFetchFailed(e.to_string()))?,
            };

            let mut kv_lock = kv.write().await;
            kv_lock.set(PreimageKey::new_keccak256(*hash).into(), code.into())?;
```

**File:** crates/proof/host/src/handler.rs (L1178-1185)
```rust
            let preimage: Bytes = providers.l2.client().request("debug_dbGet", &[hash]).await?;
            let actual_hash = keccak256(preimage.as_ref());
            if actual_hash != hash {
                return Err(HostError::StateNodePreimageHashMismatch {
                    expected: hash,
                    actual: actual_hash,
                });
            }
```

**File:** crates/proof/host/src/error.rs (L71-86)
```rust
    /// State node preimage hash mismatch.
    #[error("State node preimage hash mismatch: expected {expected}, actual {actual}")]
    StateNodePreimageHashMismatch {
        /// Expected hash.
        expected: B256,
        /// Actual hash.
        actual: B256,
    },
    /// Header preimage hash mismatch.
    #[error("Header preimage hash mismatch: expected {expected}, actual {actual}")]
    HeaderPreimageHashMismatch {
        /// Expected hash.
        expected: B256,
        /// Actual hash.
        actual: B256,
    },
```

**File:** crates/proof/proof/src/l2/chain_provider.rs (L193-208)
```rust
impl<T: CommsClient> TrieDBProvider for OracleL2ChainProvider<T> {
    fn bytecode_by_hash(&self, hash: B256) -> Result<Bytes, OracleProviderError> {
        // Fetch the bytecode preimage from the caching oracle.
        crate::block_on(async move {
            HintType::L2Code
                .with_data(&[hash.as_slice()])
                .with_data(self.chain_id.map_or_else(Vec::new, |id| id.to_be_bytes().to_vec()))
                .send(self.oracle.as_ref())
                .await?;
            self.oracle
                .get(PreimageKey::new_keccak256(*hash))
                .await
                .map(Into::into)
                .map_err(OracleProviderError::Preimage)
        })
    }
```
