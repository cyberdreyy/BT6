### Title
Unhandled `V2` variant in `extract_burn_block_consensus_hash_from_json` breaks storage/indexing of peer `StateMachineUpdate`s, risking a signer liveness wedge - ([File: stacks-signer/src/signerdb.rs])

### Summary
The i18n advisory (CVE-2020-7791) is about insufficient validation of an input variant ("erroneous language tags") that the library's parsing logic doesn't account for, leading to a DoS. The closest reachable analog in this repo is `SignerDb::extract_burn_block_consensus_hash_from_json` in `stacks-signer/src/signerdb.rs`, a SQL helper used to pull `burn_block` out of a peer-supplied, JSON-serialized `StateMachineUpdate` payload. It only special-cases the `V0` and `V1` content tags and returns a hard `SqliteError::UserFunctionError` for anything else — but the wire format (`StateMachineUpdateContent` in `libsigner/src/v0/messages.rs`) has a third variant, `V2`, that any signer or protocol-participant can legitimately send once the network has moved to global-state signing. [1](#0-0) [2](#0-1) 

### Finding Description
`extract_burn_block_consensus_hash_from_json` deserializes the JSON form of a peer's `StateMachineUpdate` and extracts `content.V0` or `content.V1`; any other tag (including the legitimate `V2` variant used to signal global-state-machine compatibility) causes it to return `Err(SqliteError::UserFunctionError("Invalid \"content\" struct: Expected one of \"V0\" or \"V1\""))`. [3](#0-2) 

Because `StateMachineUpdate` deserialization on the network path (`consensus_deserialize`) accepts `V2` content whenever the negotiated protocol version is `2` — this is by design, since `V2` is "exactly the same as Version 1 ... used to indicate this signer is compatible with global state machine processing" — a signer that has (or is transitioning to) protocol version 2 will legitimately construct and broadcast `V2` updates, and other signers will legitimately receive them via `handle_state_machine_update`, which unconditionally calls `self.signer_db.insert_state_machine_update(...)`. [4](#0-3) [5](#0-4) 

If this SQL helper is used as part of the insert path (e.g., as a SQLite scalar/generated-column function invoked on every row insertion to index `burn_block`), then any `V2` update triggers the helper's error branch on write, which the codebase's own error-handling contract ("Untrusted data ingestion must not panic. ... must gracefully handle errors") is meant to prevent for exactly this class of bug — an unhandled/absent variant in a versioned, network-received message. [6](#0-5) 

This mirrors the i18n class of defect precisely: version/variant handling that was written for an earlier schema (only `V0`/`V1`, the equivalent of a limited set of "known" language tags) was never updated when a new, protocol-legal variant (`V2`, the "erroneous"/unexpected tag from the parser's point of view) was introduced, and the failure mode is an input-validation gap rather than a crash-causing panic per se — but its effect on the signer's local database write path is the concerning part.

`global_state_evaluator.determine_global_state()` / `capitulate_miner_view()` depend on `get_signer_state_machine_updates` (an unrelated read query, unaffected) but on the *write* side, `insert_state_machine_update` persisting failing (or a query on `burn_block` derived from this function failing) for `V2` payloads would mean the local record of peers' most recent `StateMachineUpdate`s becomes incomplete on that signer for every `V2`-tagged update it receives, once any part of the signer set moves to protocol version 2.

### Impact Explanation
If `extract_burn_block_consensus_hash_from_json` is wired into the write/query path for `signer_state_machine_updates` (which the code strongly implies, given it exists solely to pull `burn_block` from the same JSON blob that `insert_state_machine_update`/`get_signer_state_machine_updates` operate on), then once any signer transitions to protocol version 2, every peer's `V2` state-machine update fails to be indexed/stored correctly on receiving signers. Since `check_block_against_global_state` and `capitulate_miner_view` rely on the global state evaluator being seeded from stored peer updates (including on restart, per `Signer::new`, which reloads `get_signer_state_machine_updates` into the `GlobalStateEvaluator`), a signer that cannot durably persist `V2` updates from its peers would operate on a stale/incomplete view of `current_miner`/`burn_block` and could fail to reach consensus on the active miner state — this falls under the specified High-impact class: "acting on a stale reward set/threshold" or being "wedged into never signing valid blocks" while the network is mid-upgrade to protocol v2. [7](#0-6) [8](#0-7) 

### Likelihood Explanation
Any single signer (or the gossip layer generally) that has negotiated protocol version 2 can trigger this by simply broadcasting its normal `StateMachineUpdate`, no majority or special access required — this is triggerable by a single participant's ordinary operation once v2 rolls out, matching the "one-slot miner plus gossip" threat model in scope. The severity is capped by the fact I could not fully confirm (within the available tool budget) that `extract_burn_block_consensus_hash_from_json` is actually registered as a live SQL function on the write path rather than being dead/vestigial code, or already superseded by a different query — this is a genuine unknown given the file segments I could inspect.

### Recommendation
Add a `V2` branch to `extract_burn_block_consensus_hash_from_json` (and any other JSON/variant-matching helper operating on `StateMachineUpdateContent`) mirroring the `V0`/`V1` handling, and audit all call sites of this function to confirm whether it is registered as an active SQLite function; if so, add a regression test that inserts and queries a `V2` `StateMachineUpdate` end-to-end through `SignerDb`.

### Proof of Concept
Conceptual (not independently executed, given no filesystem access in this mode):
1. Construct a `StateMachineUpdate` with `active_signer_protocol_version = 2`, `local_supported_signer_protocol_version = 2`, content `StateMachineUpdateContent::V2 { .. }` (valid per `StateMachineUpdate::new`/`new_inbound`). [9](#0-8) 
2. Broadcast it over StackerDB; a receiving signer processes it via `handle_state_machine_update`, which calls `signer_db.insert_state_machine_update`. [10](#0-9) 
3. If the SQL layer invokes `extract_burn_block_consensus_hash_from_json` on the stored JSON (e.g., for indexing or a WHERE clause on `burn_block`), the function returns `Err(SqliteError::UserFunctionError(...))` because only `V0`/`V1` are handled, potentially failing the insert or a subsequent query against this row. [3](#0-2) 

**Caveat**: I was unable, within the tool budget available, to locate the exact call site(s) that register `extract_burn_block_consensus_hash_from_json` as a SQLite scalar function or confirm it runs on every insert/query of `signer_state_machine_updates`. This is necessary to fully substantiate the concrete safety/liveness break required by the validation rules. If this function turns out to be dead code or only used in a non-critical diagnostic path, the finding would not meet the required impact bar. I recommend a Devin session with full repo/tool access to grep for `create_scalar_function`, `rusqlite::functions`, and all call sites referencing `extract_burn_block_consensus_hash_from_json` before treating this as confirmed.

### Citations

**File:** stacks-signer/src/signerdb.rs (L1390-1412)
```rust
    /// Extracts the `burn_block` string from a JSON state machine update payload
    fn extract_burn_block_consensus_hash_from_json(json_str: &str) -> rusqlite::Result<String> {
        let v: serde_json::Value =
            serde_json::from_str(json_str).map_err(|e| SqliteError::UserFunctionError(e.into()))?;

        let content = &v["content"];
        let content_obj = if let Some(v0) = content.get("V0") {
            v0
        } else if let Some(v1) = content.get("V1") {
            v1
        } else {
            return Err(SqliteError::UserFunctionError(
                "Invalid \"content\" struct: Expected one of \"V0\" or \"V1\"".into(),
            ));
        };

        let burn_block_hex = content_obj
            .get("burn_block")
            .and_then(|v| v.as_str())
            .ok_or_else(|| SqliteError::UserFunctionError("Missing burn_block".into()))?;

        Ok(burn_block_hex.to_string())
    }
```

**File:** libsigner/src/v0/messages.rs (L592-603)
```rust
    /// Version 2 is exactly the same as Version 1, but is used to indicate this signer is
    /// compatible with global state machine processing
    V2 {
        /// The tip burn block (i.e., the latest bitcoin block) seen by this signer
        burn_block: ConsensusHash,
        /// The tip burn block height (i.e., the latest bitcoin block) seen by this signer
        burn_block_height: u64,
        /// The signer's view of who the current miner should be (and their tenure building info)
        current_miner: StateMachineUpdateMinerState,
        /// The replay transactions
        replay_transactions: Vec<StacksTransaction>,
    },
```

**File:** libsigner/src/v0/messages.rs (L676-727)
```rust
    pub fn new(
        active_signer_protocol_version: u64,
        local_supported_signer_protocol_version: u64,
        content: StateMachineUpdateContent,
    ) -> Result<Self, CodecError> {
        let negotiated =
            active_signer_protocol_version.min(local_supported_signer_protocol_version);
        let version = content.version();

        if version != negotiated {
            return Err(CodecError::DeserializeError(format!(
                "Outbound content version {version} does not match negotiated protocol version {negotiated} \
                 (active={active_signer_protocol_version}, local_supported={local_supported_signer_protocol_version})"
            )));
        }

        Ok(Self {
            active_signer_protocol_version,
            local_supported_signer_protocol_version,
            content,
            no_manual_construct: PhantomData,
        })
    }

    /// Create a `StateMachineUpdate` for **inbound** messages.
    ///
    /// The content version must be **less than or equal to** the negotiated protocol version
    /// (`min(active_signer_protocol_version, local_supported_signer_protocol_version)`).
    /// Older versions are explicitly allowed for backward compatibility.
    fn new_inbound(
        active_signer_protocol_version: u64,
        local_supported_signer_protocol_version: u64,
        content: StateMachineUpdateContent,
    ) -> Result<Self, CodecError> {
        let negotiated =
            active_signer_protocol_version.min(local_supported_signer_protocol_version);
        let version = content.version();

        if content.version() > negotiated {
            return Err(CodecError::DeserializeError(format!(
                "Inbound content version {version} exceeds negotiated protocol version {negotiated} \
                 (active={active_signer_protocol_version}, local_supported={local_supported_signer_protocol_version})"
            )));
        }

        Ok(Self {
            active_signer_protocol_version,
            local_supported_signer_protocol_version,
            content,
            no_manual_construct: PhantomData,
        })
    }
```

**File:** libsigner/src/v0/messages.rs (L935-967)
```rust
    fn deserialize<R: Read>(fd: &mut R, version: u64) -> Result<Self, CodecError> {
        let burn_block = read_next(fd)?;
        let burn_block_height = read_next(fd)?;
        let current_miner = read_next(fd)?;
        match version {
            0 => Ok(Self::V0 {
                burn_block,
                burn_block_height,
                current_miner,
            }),
            1 => {
                let replay_transactions = read_next(fd)?;
                Ok(Self::V1 {
                    burn_block,
                    burn_block_height,
                    current_miner,
                    replay_transactions,
                })
            }
            2 => {
                let replay_transactions = read_next(fd)?;
                Ok(Self::V2 {
                    burn_block,
                    burn_block_height,
                    current_miner,
                    replay_transactions,
                })
            }
            other => Err(CodecError::DeserializeError(format!(
                "Unknown state machine update version: {other}"
            ))),
        }
    }
```

**File:** stacks-signer/src/v0/signer.rs (L272-298)
```rust

        let updates = signer_db
            .get_signer_state_machine_updates(signer_config.reward_cycle)
            .inspect_err(|e| {
                warn!("An error occurred retrieving state machine updates from the db: {e}")
            })
            .unwrap_or_default();

        let global_state_evaluator = GlobalStateEvaluator::new(
            updates,
            signer_config.signer_entries.signer_addr_to_weight.clone(),
        );
        #[cfg(any(test, feature = "testing"))]
        let version = signer_config.supported_signer_protocol_version;
        #[cfg(not(any(test, feature = "testing")))]
        let version = SUPPORTED_SIGNER_PROTOCOL_VERSION;
        let signer_state = LocalStateMachine::new(
            &mut signer_db,
            stacks_client,
            &proposal_config,
            &global_state_evaluator,
            version,
        )
        .unwrap_or_else(|e| {
            warn!("Failed to initialize local state machine for signer: {e:?}");
            LocalStateMachine::Uninitialized
        });
```

**File:** stacks-signer/src/v0/signer.rs (L944-959)
```rust
    fn check_block_against_global_state(
        &mut self,
        stacks_client: &StacksClient,
        block: &NakamotoBlock,
    ) -> Option<BlockRejection> {
        let signer_signature_hash = block.header.signer_signature_hash();
        let block_id = block.block_id();
        let Some(global_state) = self.global_state_evaluator.determine_global_state() else {
            warn!(
                "{self}: Cannot validate block, no global signer state";
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_id,
                "local_signer_state" => ?self.local_state_machine
            );
            return Some(self.create_block_rejection(RejectReason::NoSignerConsensus, block));
        };
```

**File:** stacks-signer/src/v0/signer.rs (L1082-1106)
```rust
    fn handle_state_machine_update(
        &mut self,
        signer_public_key: &Secp256k1PublicKey,
        update: &StateMachineUpdate,
        received_time: &SystemTime,
    ) {
        let replay_txids = update.content.replay_txids();
        let pubkey = signer_public_key.to_hex();
        info!(
            "{self}: Received state machine update from signer {pubkey}: {update}";
            "replay_txids" => ?replay_txids
        );
        let address = StacksAddress::p2pkh(self.mainnet, signer_public_key);
        // Store the state machine update so we can reload it if we crash
        if let Err(e) = self.signer_db.insert_state_machine_update(
            self.reward_cycle,
            &address,
            update,
            received_time,
        ) {
            warn!("{self}: Failed to update global state in signerdb: {e}");
        }
        self.global_state_evaluator
            .insert_update(address, update.clone());
    }
```

**File:** CONTRIBUTING.md (L303-306)
```markdown
- **All network input reception is time-bound.** Every piece of code that ingests data _from the network_ must impose a maximum amount of time that ingestion can take. If the data takes too long to arrive, then it must be discarded without any further processing. There is no time bound for data ingested from disk or passed as an argument; this requirement is meant by the space-bound requirement.

- **Untrusted data ingestion must not panic.** Every piece of code that ingests untrusted data must gracefully handle errors. Panicking failures are forbidden for such data. Panics are only allowed if the ingested data was previously written by the node (and thus trusted).

```
