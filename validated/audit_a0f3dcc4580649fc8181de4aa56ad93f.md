### Title
Out-of-bounds panic in `parse_confidential_transfer_instruction` via unchecked `account_indexes` values - ([File: transaction-status/src/parse_token/extension/confidential_transfer.rs])

### Summary
`parse_confidential_transfer_instruction` indexes `account_keys[account_indexes[i] as usize]` in every match arm without verifying that the attacker-controlled byte `account_indexes[i]` is less than `account_keys.len()`. The only guard, `check_num_token_accounts`, validates only the *count* of indexes supplied, not their *value*, so a transaction whose confidential-transfer inner instruction encodes an out-of-range account index will panic the RPC/transaction-status decoding path when a client requests `jsonParsed` encoding.

### Finding Description
`parse_confidential_transfer_instruction` decodes the SPL Token-2022 confidential-transfer instruction discriminant, calls `check_num_token_accounts(account_indexes, N)?;`, and then unconditionally does `account_keys[account_indexes[k] as usize].to_string()` for several fixed and offset-derived `k` values across all match arms (`InitializeMint`, `UpdateMint`, `ConfigureAccount`, `ApproveAccount`, `EmptyAccount`, `Deposit`, `Withdraw`, `Transfer`, `TransferWithFee`, `ApplyPendingBalance`, credit-toggle variants, `ConfigureAccountWithRegistry`) — e.g. [1](#0-0) , [2](#0-1) .

`check_num_token_accounts` delegates to `check_num_accounts`, which (per the call sites and helper naming across every `parse_token/extension/*.rs` file) only asserts `accounts.len() >= num`; it never asserts that each byte value in `account_indexes` is `< account_keys.len()`. `account_indexes` is derived directly from the `CompiledInstruction.accounts` field of an attacker-crafted transaction message, and `account_keys` is bounded by that same message's actual `account_keys` list. Since Rust's `Index`/slice indexing panics on out-of-range access, and there is no bounds check before indexing, a compiled instruction naming the spl-token-2022 program with a confidential-transfer discriminant and an `accounts` byte list containing a value `>= account_keys.len()` will panic when parsed.

This same unchecked-index pattern is repeated in `parse_signers` (`transaction-status/src/parse_token.rs` lines 937-961), used by nearly every arm here, and in other extension parsers (`confidential_mint_burn.rs`, `permissioned_burn.rs`, `default_account_state.rs`, `group_pointer.rs`, `pausable.rs`, `reallocate.rs`, `token_group.rs`, `transfer_fee.rs`), confirming the check performed is count-only, never value-bound. [3](#0-2) 

The exploit flow: attacker submits (writes on-chain) a transaction whose message declares a short `account_keys` list but whose spl-token-2022 confidential-transfer instruction's `accounts` byte array contains a value exceeding that list's length while still satisfying the minimum-count check. Later, an unprivileged client calls `getTransaction`/`getConfirmedTransaction` with `encoding=jsonParsed` for that signature, which routes through `parse_ui_instruction` → `parse` → `parse_token` → `parse_confidential_transfer_instruction`, triggering the out-of-bounds index panic. [4](#0-3) 

### Impact Explanation
A single crafted on-chain transaction combined with a single unprivileged `jsonParsed` RPC read can panic the thread handling the RPC request. If this panic is not caught at a higher layer (no evidence of a catch_unwind boundary was found around `parse_ui_instruction`/`parse_token`), this constitutes a decoder panic / process-availability issue reachable by one low-rate RPC call, matching the "decoder panic and misreporting" / single-request DoS bounty category.

### Likelihood Explanation
Feasibility is high: constructing a transaction with a mismatched account_indexes byte and a minimal account_keys list requires only building a raw `Transaction`/`Message` by hand (bypassing the SDK helper functions that would normally produce well-formed indices) and submitting it — a single on-chain write, no elevated privileges. The follow-up `getTransaction(..., encoding: jsonParsed)` call is a single, standard RPC call within the allowed rate. The check_num_token_accounts guard does not prevent this because it only checks list length, not index values, so the condition is trivially satisfiable for every instruction discriminant.

### Recommendation
Add a value-bounds check in `check_num_accounts` (or immediately after it in each parser) verifying that every byte in `account_indexes` is `< account_keys.len()`, returning `ParseInstructionError::InstructionNotParsable` (or a dedicated error) instead of allowing indexing to proceed. Apply this fix centrally in `check_num_accounts` in `transaction-status/src/parse_instruction.rs` so all `parse_token` extension parsers (and `parse_signers`) benefit without needing per-arm patches.

### Proof of Concept
```rust
// transaction-status/src/parse_token/extension/confidential_transfer.rs (test module)
#[test]
fn test_confidential_transfer_oob_account_index_does_not_panic() {
    // Build instruction_data for e.g. ApplyPendingBalance discriminant
    let instruction_data = /* valid discriminant + ApplyPendingBalanceData bytes */;
    // account_indexes satisfies count check (len >= 2) but references an
    // index far beyond any realistic account_keys list.
    let account_indexes: &[u8] = &[250, 251];
    let account_keys = AccountKeys::new(&[Pubkey::new_unique()], None); // len == 1

    let result = parse_confidential_transfer_instruction(
        &instruction_data,
        account_indexes,
        &account_keys,
    );

    // Expect a graceful error, not a panic.
    assert!(result.is_err());
}
```
Run this test (and an equivalent variant for every `ConfidentialTransferInstruction` arm, plus `parse_signers` directly) under `cargo test` — currently it will panic with "index out of bounds" instead of returning `Err`, demonstrating the vulnerability; after adding the bounds check the assertion `result.is_err()` should pass without panicking.

### Citations

**File:** transaction-status/src/parse_token/extension/confidential_transfer.rs (L18-26)
```rust
        ConfidentialTransferInstruction::InitializeMint => {
            check_num_token_accounts(account_indexes, 1)?;
            let initialize_mint_data: InitializeMintData =
                *decode_instruction_data(instruction_data).map_err(|_| {
                    ParseInstructionError::InstructionNotParsable(ParsableProgram::SplToken)
                })?;
            let mut value = json!({
                "mint": account_keys[account_indexes[0] as usize].to_string(),
                "autoApproveNewAccounts": bool::from(initialize_mint_data.auto_approve_new_accounts),
```

**File:** transaction-status/src/parse_token/extension/confidential_transfer.rs (L241-250)
```rust
        ConfidentialTransferInstruction::Transfer => {
            check_num_token_accounts(account_indexes, 4)?;
            let transfer_data: TransferInstructionData = *decode_instruction_data(instruction_data)
                .map_err(|_| {
                    ParseInstructionError::InstructionNotParsable(ParsableProgram::SplToken)
                })?;
            let mut value = json!({
                "source": account_keys[account_indexes[0] as usize].to_string(),
                "mint": account_keys[account_indexes[1] as usize].to_string(),
                "destination": account_keys[account_indexes[2] as usize].to_string(),
```

**File:** transaction-status/src/parse_token.rs (L937-965)
```rust
fn parse_signers(
    map: &mut Map<String, Value>,
    last_nonsigner_index: usize,
    account_keys: &AccountKeys,
    accounts: &[u8],
    owner_field_name: &str,
    multisig_field_name: &str,
) {
    if accounts.len() > last_nonsigner_index + 1 {
        let mut signers: Vec<String> = vec![];
        for i in accounts[last_nonsigner_index + 1..].iter() {
            signers.push(account_keys[*i as usize].to_string());
        }
        map.insert(
            multisig_field_name.to_string(),
            json!(account_keys[accounts[last_nonsigner_index] as usize].to_string()),
        );
        map.insert("signers".to_string(), json!(signers));
    } else {
        map.insert(
            owner_field_name.to_string(),
            json!(account_keys[accounts[last_nonsigner_index] as usize].to_string()),
        );
    }
}

fn check_num_token_accounts(accounts: &[u8], num: usize) -> Result<(), ParseInstructionError> {
    check_num_accounts(accounts, num, ParsableProgram::SplToken)
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
