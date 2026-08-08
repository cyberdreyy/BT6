### Title
Unbounded `bincode::deserialize` of `SystemInstruction` in `parse_system` allows oversized allocation from a single `getTransaction(jsonParsed)` call - ([File: transaction-status/src/parse_system.rs])

### Summary
`parse_system` deserializes untrusted, on-chain-recorded `instruction.data` using plain `bincode::deserialize`, with no size/length limit, unlike sibling code paths for the same `SystemInstruction` type that use `solana_bincode::limited_deserialize` bounded to `PACKET_DATA_SIZE`. Because `bincode`'s default (unbounded) deserializer reads a `u64`/varint length prefix for `String`/`Vec` fields (e.g. `CreateAccountWithSeed { seed: String, .. }`) and attempts to allocate/read that many bytes before validating it against the actual remaining buffer, an attacker can craft a confirmed transaction whose instruction bytes declare an enormous `seed` length, causing an oversized allocation attempt when any client calls `getTransaction` with `jsonParsed` encoding.

### Finding Description
`parse_system` is the parser invoked by the transaction-status "jsonParsed" instruction decoding path for `system_program` instructions: [1](#0-0) 

It calls `bincode::deserialize(&instruction.data)` directly, with no explicit size limit configured. Compare this to every other place in the codebase that deserializes the exact same `SystemInstruction` enum from untrusted instruction bytes, all of which explicitly bound the deserializer to `PACKET_DATA_SIZE` via `solana_bincode::limited_deserialize`:

- `system_processor.rs` (on-chain execution) uses `solana_bincode::limited_deserialize`. [2](#0-1) 
- `cli-output/src/display.rs` explicitly bounds `SystemInstruction`, `VoteInstruction`, and `StakeInstruction` decoding to `solana_packet::PACKET_DATA_SIZE`. [3](#0-2) 
- `cost-model/src/cost_model.rs` and `vote/src/vote_parser.rs` follow the same bounded pattern. [4](#0-3) [5](#0-4) 

`parse_system.rs`, however, is the only path that decodes this same untrusted enum with plain, unbounded `bincode::deserialize`. Bincode's default (unbounded) configuration reads the serialized length prefix for `String`/`Vec<u8>` fields and attempts to pre-allocate a buffer of that declared size before it can check the number of remaining bytes in the input slice. Since `SystemInstruction::CreateAccountWithSeed { base, seed: String, lamports, space, owner }` contains a `String` field, an attacker only needs to place a `CreateAccountWithSeed` discriminant followed by an inflated length varint as the recorded `instruction.data` of a transaction (the transaction can otherwise fail execution — the "included but failed" bytes are still recorded in the blockstore and returned by `getTransaction`). When any single client calls `getTransaction` (or `getConfirmedTransaction`/`getBlock`) with `encoding: jsonParsed`, the parser dispatches into `parse_system`, which attempts the oversized allocation before returning an error, all within a single unprivileged RPC call.

The `limited_deserialize` guard used elsewhere caps the size the bincode deserializer will permit for any field lengths to `PACKET_DATA_SIZE` (1232 bytes) before allocation, which prevents this exact class of issue. `parse_system` lacks this guard, so the fix is a straightforward application of an already-existing, already-used Agave utility — not a change to the `bincode` crate itself.

### Impact Explanation
A single unprivileged `getTransaction(jsonParsed)` call against a transaction containing crafted `system_program` instruction bytes can trigger an oversized allocation attempt inside the RPC-serving thread of the validator's transaction-status decode path. Depending on the allocator/OS behavior, this can result in a large transient memory spike, an allocation failure/abort, or degraded service for that single request — this matches the "unbounded cost for a single low-rate call" / decoder misbehavior category rather than full RCE.

### Likelihood Explanation
Feasible with a single unprivileged actor: the attacker only needs to get any transaction (even one that fails to execute) that includes a `system_program` instruction with the crafted byte pattern committed to a confirmed block, and then issue one `getTransaction(jsonParsed)` request. No special stake, leadership, or elevated access is required, and the call rate needed is one call, well under the `CLUSTER_SLOT_TIME_TARGET / 2` throttle.

### Recommendation
In `transaction-status/src/parse_system.rs`, replace `bincode::deserialize(&instruction.data)` with `solana_bincode::limited_deserialize(&instruction.data, solana_packet::PACKET_DATA_SIZE as u64)`, mirroring the pattern already used in `cli-output/src/display.rs`, `programs/system/src/system_processor.rs`, `cost-model/src/cost_model.rs`, and `vote/src/vote_parser.rs`. Audit other `transaction-status/src/parse_*.rs` parsers (e.g. `parse_bpf_loader.rs`, which also calls raw `bincode::deserialize`) for the same unbounded-deserialize pattern and apply the same fix consistently.

### Proof of Concept
```rust
// transaction-status/src/parse_system.rs (test module)
#[test]
fn test_parse_create_account_with_seed_oversized_length_prefix() {
    use solana_message::{AccountKeys, compiled_instruction::CompiledInstruction};

    // Craft raw bytes: bincode-encoded enum discriminant for
    // SystemInstruction::CreateAccountWithSeed (variant index as u32),
    // followed by an inflated bincode length prefix (u64::MAX) for the
    // `seed: String` field, with no actual payload bytes following.
    let mut data = Vec::new();
    data.extend_from_slice(&3u32.to_le_bytes()); // CreateAccountWithSeed variant index
    data.extend_from_slice(&[0u8; 32]);          // base: Pubkey
    data.extend_from_slice(&u64::MAX.to_le_bytes()); // seed length prefix (inflated)
    // No further bytes: actual buffer is far smaller than declared length.

    let instruction = CompiledInstruction {
        program_id_index: 0,
        accounts: vec![0, 1],
        data,
    };
    let account_keys = AccountKeys::new(&[/* system_program::id(), source, new_account */], None);

    // Expected (safe) behavior: fails fast with InstructionNotParsable,
    // bounded allocation attempt (<= PACKET_DATA_SIZE), no huge alloc.
    // Actual (vulnerable) behavior with plain bincode::deserialize:
    // attempts to allocate/read up to u64::MAX bytes for the String
    // before failing, causing a large transient allocation.
    let result = parse_system(&instruction, &account_keys);
    assert!(result.is_err());
    // Fuzz/invariant target: instrument allocator (e.g. via a custom
    // GlobalAlloc counter in a fuzz harness) and assert
    // max_single_allocation_bytes <= instruction.data.len()
    // (or <= PACKET_DATA_SIZE after the fix using limited_deserialize).
}
```
Fuzz plan: build a `cargo fuzz` target that feeds arbitrary bytes as `instruction.data` with the `CreateAccountWithSeed` discriminant prefix and a randomized/adversarial length varint for `seed`, wrap the global allocator to record the largest single allocation request observed during `parse_system`, and assert it never exceeds `instruction.data.len()` (post-fix, it should be bounded by `PACKET_DATA_SIZE` via `limited_deserialize`).

### Citations

**File:** transaction-status/src/parse_system.rs (L11-16)
```rust
pub fn parse_system(
    instruction: &CompiledInstruction,
    account_keys: &AccountKeys,
) -> Result<ParsedInstructionEnum, ParseInstructionError> {
    let system_instruction: SystemInstruction = deserialize(&instruction.data)
        .map_err(|_| ParseInstructionError::InstructionNotParsable(ParsableProgram::System))?;
```

**File:** programs/system/src/system_processor.rs (L1-19)
```rust
use {
    crate::system_instruction::{
        advance_nonce_account, authorize_nonce_account, initialize_nonce_account,
        withdraw_nonce_account,
    },
    log::*,
    solana_bincode::limited_deserialize,
    solana_instruction::error::InstructionError,
    solana_nonce as nonce,
    solana_program_runtime::{
        declare_process_instruction, invoke_context::InvokeContext,
        sysvar_cache::get_sysvar_with_account_check,
    },
    solana_pubkey::Pubkey,
    solana_sdk_ids::system_program,
    solana_svm_log_collector::ic_msg,
    solana_system_interface::{
        MAX_PERMITTED_DATA_LENGTH, error::SystemError, instruction::SystemInstruction,
    },
```

**File:** cli-output/src/display.rs (L473-482)
```rust
        } else if program_pubkey == &solana_sdk_ids::system_program::id() {
            if let Ok(system_instruction) =
                limited_deserialize::<solana_system_interface::instruction::SystemInstruction>(
                    &instruction.data,
                    solana_packet::PACKET_DATA_SIZE as u64,
                )
            {
                writeln!(w, "{prefix}  {system_instruction:?}")?;
                raw = false;
            }
```

**File:** cost-model/src/cost_model.rs (L8-20)
```rust
use {
    crate::{block_cost_limits::*, transaction_cost::*},
    agave_feature_set::FeatureSet,
    solana_bincode::limited_deserialize,
    solana_compute_budget::compute_budget_limits::DEFAULT_HEAP_COST,
    solana_pubkey::Pubkey,
    solana_runtime_transaction::transaction_meta::TransactionMeta,
    solana_sdk_ids::system_program,
    solana_svm_transaction::{instruction::SVMInstruction, svm_message::SVMStaticMessage},
    solana_system_interface::{
        MAX_PERMITTED_ACCOUNTS_DATA_ALLOCATIONS_PER_TRANSACTION, MAX_PERMITTED_DATA_LENGTH,
        instruction::SystemInstruction,
    },
```

**File:** vote/src/vote_parser.rs (L30-32)
```rust
    limited_deserialize::<VoteInstruction>(instruction.data, solana_packet::PACKET_DATA_SIZE as u64)
        .map(|ix| ix.is_single_vote_state_update())
        .unwrap_or(false)
```
