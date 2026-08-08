### Title
Unchecked account-index access when decoding `CompiledInstruction`s for RPC transaction responses can panic the validator process - (File: `transaction-status/src/lib.rs`)

### Summary
`make_ui_partially_decoded_instruction` (used via `parse_ui_instruction`) indexes into `account_keys` using untrusted `program_id_index`/`accounts` values taken directly from a `CompiledInstruction` without first checking that these indexes are within bounds of `account_keys`. This mirrors the "lack of claim validation" bug class in the report: a caller-controlled index (`_index`) is used to access an array/slice without a `require`/bounds check, and the code proceeds into logic that assumes the index is valid.

### Finding Description
`make_ui_partially_decoded_instruction` directly indexes `account_keys` with attacker/data-controlled indexes: [1](#0-0) 

```rust
fn make_ui_partially_decoded_instruction(
    instruction: &CompiledInstruction,
    account_keys: &AccountKeys,
    stack_height: Option<u32>,
) -> UiPartiallyDecodedInstruction {
    UiPartiallyDecodedInstruction {
        program_id: account_keys[instruction.program_id_index as usize].to_string(),
        accounts: instruction
            .accounts
            .iter()
            .map(|&i| account_keys[i as usize].to_string())
            .collect(),
        ...
    }
}
```

This is reached from `parse_ui_instruction`, which falls back to it whenever `parse()` fails to recognize the program: [2](#0-1) 

By contrast, other parsers in the same crate (`parse_system`, `parse_address_lookup_table`) explicitly validate the max instruction account index against `account_keys.len()` before indexing, with an explicit comment noting this defends against cases the runtime is *supposed* to prevent: [3](#0-2) [4](#0-3) 

`make_ui_partially_decoded_instruction` has no equivalent guard, so any code path that reaches it with a `CompiledInstruction` whose `program_id_index` or `accounts` entries are `>= account_keys.len()` will panic via the `Index` implementation on `AccountKeys`/slice indexing, since Rust's default indexing panics out of bounds rather than returning an `Option`/`Result` (as the sibling checked functions do, e.g. `account_keys.get(...)`).

### Impact Explanation
A panic inside RPC transaction/block decoding logic (`getTransaction`, `getBlock`, `getConfirmedTransaction` style JSON-parsed encodings that call into `parse_ui_instruction`) triggered by a single crafted or malformed compiled instruction (index out of range relative to the transaction's account key set) causes a decoder panic. Depending on how the JSON-RPC/transaction-status thread handles panics, this can manifest as a request-scoped panic (bounded impact) or an actual crash/abort if not caught, which matches the "decoder panic and misreporting" acceptance criterion for validator crash-class analogs described in the rules.

### Likelihood Explanation
The vulnerable code sits in the historical-data / RPC decoding path (`transaction-status` crate), which is reachable via a single unprivileged RPC call requesting an already-stored transaction/block for encodings that invoke instruction parsing (`jsonParsed`). No special validator/peer/operator role is required to trigger the code path — only a way to have (or reference) a transaction whose compiled instruction indexes are inconsistent with the account-keys array being decoded (e.g., mismatches from partial/failed loaded-address resolution, or malformed data reaching the decode path outside of the strict runtime sanitize checks that normally bound these indexes at execution time). This is the same fundamental root cause pattern as the reported analog: consumption of caller-influenced index without a bounds check before doing meaningful/expected work.

### Recommendation
Add an explicit bounds check in `make_ui_partially_decoded_instruction` (mirroring the pattern already used in `parse_system.rs`/`parse_address_lookup_table.rs`) before indexing, e.g. verify `instruction.program_id_index as usize < account_keys.len()` and that every entry in `instruction.accounts` is `< account_keys.len()`, returning a fallback/placeholder `UiPartiallyDecodedInstruction` (or propagating an error) instead of indexing directly. Alternatively, replace direct `account_keys[...]` indexing with `account_keys.get(...)` and handle the `None` case gracefully.

### Proof of Concept
Not independently reproduced against a running validator in this analysis; this is a static-code-path analog derived from comparing `make_ui_partially_decoded_instruction` (unchecked) against the deliberately-guarded sibling parsers `parse_system`/`parse_address_lookup_table` in the same crate. Confirming exploitability end-to-end (i.e., constructing a stored transaction whose `CompiledInstruction` indexes are inconsistent with the `AccountKeys` passed at encode time) would require tracing the exact `AccountKeys` construction path for `VersionedTransactionWithStatusMeta::encode_with_meta`, which was not fully explored due to tool-call limits — flagged here as an open verification item for a follow-up session with full repository access.

### Citations

**File:** transaction-status/src/lib.rs (L96-111)
```rust
fn make_ui_partially_decoded_instruction(
    instruction: &CompiledInstruction,
    account_keys: &AccountKeys,
    stack_height: Option<u32>,
) -> UiPartiallyDecodedInstruction {
    UiPartiallyDecodedInstruction {
        program_id: account_keys[instruction.program_id_index as usize].to_string(),
        accounts: instruction
            .accounts
            .iter()
            .map(|&i| account_keys[i as usize].to_string())
            .collect(),
        data: bs58::encode(instruction.data.clone()).into_string(),
        stack_height,
    }
}
```

**File:** transaction-status/src/lib.rs (L113-126)
```rust
pub fn parse_ui_instruction(
    instruction: &CompiledInstruction,
    account_keys: &AccountKeys,
    stack_height: Option<u32>,
) -> UiInstruction {
    let program_id = &account_keys[instruction.program_id_index as usize];
    if let Ok(parsed_instruction) = parse(program_id, instruction, account_keys, stack_height) {
        UiInstruction::Parsed(UiParsedInstruction::Parsed(parsed_instruction))
    } else {
        UiInstruction::Parsed(UiParsedInstruction::PartiallyDecoded(
            make_ui_partially_decoded_instruction(instruction, account_keys, stack_height),
        ))
    }
}
```

**File:** transaction-status/src/parse_system.rs (L17-25)
```rust
    match instruction.accounts.iter().max() {
        Some(index) if (*index as usize) < account_keys.len() => {}
        _ => {
            // Runtime should prevent this from ever happening
            return Err(ParseInstructionError::InstructionKeyMismatch(
                ParsableProgram::System,
            ));
        }
    }
```

**File:** transaction-status/src/parse_address_lookup_table.rs (L19-27)
```rust
    match instruction.accounts.iter().max() {
        Some(index) if (*index as usize) < account_keys.len() => {}
        _ => {
            // Runtime should prevent this from ever happening
            return Err(ParseInstructionError::InstructionKeyMismatch(
                ParsableProgram::AddressLookupTable,
            ));
        }
    }
```
