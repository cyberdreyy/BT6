### Title
Unchecked array indexing on `account_keys` in `parse_ui_instruction` / `make_ui_partially_decoded_instruction` can panic the RPC/validator process when decoding a transaction with an out-of-range instruction index - (File: transaction-status/src/lib.rs)

### Summary
The reported analog bug class is an out-of-range array index that is not bounds-checked, causing a revert/DoS when the maximum expected index is reached. The equivalent pattern in agave is direct `Index` (`[]`) access into the `account_keys: &AccountKeys` slice inside `make_ui_partially_decoded_instruction` and `parse_ui_instruction`, used when the RPC/transaction-status layer converts a `CompiledInstruction` into its JSON-RPC representation for `getTransaction`, `getConfirmedBlock`, inner-instruction encoding, etc.

### Finding Description
`make_ui_partially_decoded_instruction` indexes `account_keys` with `instruction.program_id_index` and every entry of `instruction.accounts` without any bounds check: [1](#0-0) 

`parse_ui_instruction`, which is invoked while encoding both top-level and inner instructions for RPC transaction responses, also directly indexes `account_keys[instruction.program_id_index as usize]` before attempting to parse the instruction: [2](#0-1) 

This is called from `parse_ui_inner_instructions`, reachable from `parse_ui_transaction_status_meta`, which builds `account_keys` from `static_keys` and `meta.loaded_addresses` when encoding a transaction's status metadata for RPC output (e.g. `getTransaction`): [3](#0-2) [4](#0-3) 

By contrast, every dedicated instruction parser in this crate (`parse_system`, `parse_address_lookup_table`, `parse_associated_token`, etc.) explicitly validates indices before indexing, e.g.: [5](#0-4) [6](#0-5) 

This shows the codebase is aware that `CompiledInstruction` account/program indices must be validated against `account_keys.len()` before use — but `make_ui_partially_decoded_instruction` and `parse_ui_instruction`'s own `program_id_index` access skip this check entirely, mirroring exactly the `burnFees[feeCycle.length]` out-of-range pattern from the report (an index derived from instruction/message structure used to directly subscript an array without a bounds guard).

### Impact Explanation
If a `CompiledInstruction` with a `program_id_index` or `accounts` entries exceeding `account_keys.len()` reaches this encoding path (e.g., via historical/legacy blockstore or BigTable-stored transaction data, or via any code path that does not perform the same sanitize/`SanitizedMessage` validation that live-transaction processing enforces), indexing will panic instead of returning an error. Because this runs inside the JSON-RPC handler thread(s) used to serve `getTransaction`/`getConfirmedBlock`/`getSignaturesForAddress`-linked encoding, a single crafted or historically malformed transaction could crash the request-handling thread, denying that RPC functionality — analogous to the reported "function reversion after 14 days" denial-of-service class, but here manifesting as a decoder panic rather than a revert.

### Likelihood Explanation
Under normal conditions, `CompiledInstruction` indices are validated by `SanitizedMessage`/transaction sanitization before they reach the runtime, so instructions produced from live transaction processing should already satisfy `index < account_keys.len()`. The residual risk is confined to code paths that reconstruct instructions from stored/replayed data without re-validating (e.g., legacy blockstore entries, ledger-tool utilities, or bigtable-backed historical transaction lookups) where such invariants may not be guaranteed to hold as strictly as for freshly-sanitized transactions. I could not fully verify within the available index whether any currently-reachable RPC/blockstore path can supply an un-sanitized `CompiledInstruction` to this exact function, so likelihood is uncertain and this should be validated against the actual call sites and sanitize-time guarantees before treating it as a confirmed crash vector.

### Recommendation
Mirror the pattern already used by `parse_system`, `parse_address_lookup_table`, and similar functions: validate `instruction.program_id_index` and every entry of `instruction.accounts` against `account_keys.len()` before indexing in `make_ui_partially_decoded_instruction` and `parse_ui_instruction`, returning a decode error (e.g., via `UiInstruction`'s error path) instead of panicking on out-of-range indices.

### Proof of Concept
Not independently verified against a live reachable trigger; the code-level PoC is: construct a `CompiledInstruction` whose `program_id_index` (or any `accounts[i]`) is `>= account_keys.len()`, then invoke `parse_ui_instruction`/`make_ui_partially_decoded_instruction` on it — this will panic on `Index` out of bounds rather than return an error. Confirming exploitability requires demonstrating a real caller (blockstore replay, bigtable historical fetch, or RPC request) that supplies such unsanitized instruction data, which was not confirmed within the scope of this analysis.

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

**File:** transaction-status/src/lib.rs (L145-166)
```rust
        })
        .filter(|i| !i.instructions.is_empty())
}

pub fn parse_ui_inner_instructions(
    inner_instructions: InnerInstructions,
    account_keys: &AccountKeys,
) -> UiInnerInstructions {
    UiInnerInstructions {
        index: inner_instructions.index,
        instructions: inner_instructions
            .instructions
            .iter()
            .map(
                |InnerInstruction {
                     instruction: ix,
                     stack_height,
                 }| { parse_ui_instruction(ix, account_keys, *stack_height) },
            )
            .collect(),
    }
}
```

**File:** transaction-status/src/lib.rs (L200-219)
```rust
fn parse_ui_transaction_status_meta(
    meta: TransactionStatusMeta,
    static_keys: &[Pubkey],
    show_rewards: bool,
) -> UiTransactionStatusMeta {
    let account_keys = AccountKeys::new(static_keys, Some(&meta.loaded_addresses));
    UiTransactionStatusMeta {
        err: meta.status.clone().map_err(Into::into).err(),
        status: meta.status.map_err(Into::into),
        fee: meta.fee,
        pre_balances: meta.pre_balances,
        post_balances: meta.post_balances,
        inner_instructions: meta
            .inner_instructions
            .map(|ixs| {
                ixs.into_iter()
                    .map(|ix| parse_ui_inner_instructions(ix, &account_keys))
                    .collect()
            })
            .into(),
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
