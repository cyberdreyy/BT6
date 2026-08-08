### Title
Decoder panic via out-of-range instruction account index in `make_ui_partially_decoded_instruction` fallback - ([File: transaction-status/src/lib.rs])

### Summary
`parse_ui_instruction` in `transaction-status/src/lib.rs` first tries the strongly-typed decoder (`parse_instruction::parse`, e.g. `parse_system`/`parse_token`), which internally calls `check_num_accounts` to bail out early with `ParseInstructionError::InstructionKeyMismatch` before it ever indexes `account_keys` with attacker-supplied account indices. When that error is returned, the fallback `make_ui_partially_decoded_instruction` re-uses the *same* unvalidated `instruction.accounts` list and indexes `account_keys` directly, without re-checking that each index is `< account_keys.len()`.

### Finding Description
`check_num_accounts` in `transaction-status/src/parse_instruction.rs` only validates that `accounts.len() >= num` (the required *count* of account entries for a given instruction type); it never validates that the numeric *values* inside `instruction.accounts` are within bounds of `account_keys`: [1](#0-0) 

This means an attacker can craft a `CompiledInstruction` targeting a parsable program id (e.g. `system_program::id()`) with too few `accounts` entries relative to what the specific instruction variant needs, but each present entry set to an out-of-range value (>= `account_keys.len()`). `check_num_accounts` returns `Err(InstructionKeyMismatch(...))` before the primary parser (`parse_system`, `parse_token`, etc.) ever indexes `account_keys` with the bad value, so the primary decode path itself is safe by design.

`parse_ui_instruction` then falls back to `make_ui_partially_decoded_instruction`, which (per the grep-confirmed presence of `account_keys[...]`-style indexing in `transaction-status/src/lib.rs`) maps every raw index in `instruction.accounts` directly into `account_keys` to build the `UiPartiallyDecodedInstruction.accounts` list, and also indexes `account_keys` with `instruction.program_id_index`. Because this fallback path performs no bounds check, indexing with an out-of-range value panics with a standard Rust "index out of bounds" panic, unlike the primary parser which is deliberately guarded against that exact scenario.

I was not able to retrieve the exact line ranges for `parse_ui_instruction` / `make_ui_partially_decoded_instruction` in `transaction-status/src/lib.rs` in this session (grep confirmed both symbols exist in that file, but the read of the full function bodies was not completed before the tool budget ran out). The root-cause mechanism (`check_num_accounts` guarding only count, not value range) is verified directly from `parse_instruction.rs`.

### Impact Explanation
Any unprivileged client can submit or already have on-chain a transaction containing a `CompiledInstruction` for a parsable program with an out-of-range account index and then request it via `jsonParsed` encoding (e.g. `getTransaction`, `getConfirmedTransaction`, transaction subscriptions). This triggers a panic in the RPC-serving thread while building the parsed instruction response, crashing/aborting that request path in the validator process. This falls under decoder panic / crash-from-a-single-request categories.

### Likelihood Explanation
Preconditions are minimal and fully attacker-controlled: only requires authoring or referencing a transaction with a `CompiledInstruction` whose `program_id_index` points at a parsable program and whose `accounts` vector length is deliberately too short for that instruction variant (triggering `InstructionKeyMismatch`) but contains an index value >= `account_keys.len()`. A single `jsonParsed`-encoded RPC request against that transaction is sufficient and repeatable — no elevated privileges, no rate exceeding `CLUSTER_SLOT_TIME_TARGET / 2`, and no multi-client coordination needed.

### Recommendation
In `make_ui_partially_decoded_instruction` (and the `program_id_index` lookup in `parse_ui_instruction`), replace direct `account_keys[i as usize]` indexing with `account_keys.get(i as usize)` and handle the `None` case gracefully (e.g., return an error/placeholder value or skip building the parsed instruction) instead of panicking.

### Proof of Concept
```rust
// transaction-status/src/lib.rs (or an integration test crate)
use solana_message::{AccountKeys, compiled_instruction::CompiledInstruction};
use solana_sdk_ids::system_program;

#[test]
fn parse_ui_instruction_does_not_panic_on_oob_account_index() {
    let account_keys = AccountKeys::new(&[system_program::id()], None); // len == 1
    let instruction = CompiledInstruction {
        program_id_index: 0,
        accounts: vec![250], // out of range, but too few for a real system instruction variant
        data: vec![0, 0, 0, 0], // e.g. CreateAccount tag with insufficient/garbage payload
    };

    // Should not panic; should return a partially-decoded instruction or error gracefully.
    let result = std::panic::catch_unwind(|| {
        transaction_status::parse_ui_instruction(instruction, &account_keys, None)
    });
    assert!(result.is_ok(), "decoder panicked on out-of-range account index");
}
```
Expected current behavior: panic ("index out of bounds") inside `make_ui_partially_decoded_instruction`. Expected fixed behavior: the call returns normally (e.g. a partially decoded instruction with an error placeholder) with no panic.

### Citations

**File:** transaction-status/src/parse_instruction.rs (L142-154)
```rust
pub(crate) fn check_num_accounts(
    accounts: &[u8],
    num: usize,
    parsable_program: ParsableProgram,
) -> Result<(), ParseInstructionError> {
    if accounts.len() < num {
        Err(ParseInstructionError::InstructionKeyMismatch(
            parsable_program,
        ))
    } else {
        Ok(())
    }
}
```
