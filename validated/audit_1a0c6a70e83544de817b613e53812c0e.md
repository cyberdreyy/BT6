### Title
Withdraw offset-based account labeling mislabels a proof-context account as "owner" when `account_indexes` length is insufficient for the encoded offsets - (File: transaction-status/src/parse_token/extension/confidential_transfer.rs)

### Summary
In `parse_confidential_transfer_instruction`'s `Withdraw` branch, the account-role assignment walks through `instructionsSysvar` / `equalityProofContextStateAccount` / `rangeProofContextStateAccount` slots using a running `offset` bounded only by `account_indexes.len().saturating_sub(1)`. Because `check_num_token_accounts` only enforces a minimum of 4 accounts (not the number actually required by the combination of `equality_proof_instruction_offset`/`range_proof_instruction_offset`), an attacker can supply exactly 4 accounts with offset combinations that require 5, causing a genuine proof-context account to be silently skipped and the last supplied index to be mislabeled as `"owner"` in the parsed JSON.

### Finding Description
The Withdraw branch is: [1](#0-0) 

`check_num_token_accounts(account_indexes, 4)` only guarantees `account_indexes.len() >= 4`; it does not validate that the number of accounts matches what `equality_proof_instruction_offset`/`range_proof_instruction_offset` imply. With `account_indexes.len() == 4`, the boundary `account_indexes.len().saturating_sub(1) == 3` allows only one optional slot (`offset == 2`) to be filled before `offset` becomes `3` and the `< 3` checks fail for the remaining optional fields, at which point `parse_signers(map, offset, ...)` labels `account_indexes[3]` as `"owner"`.

Concretely, for the boundary case `account_indexes.len() == 4`:
- `equality_proof_instruction_offset != 0`, `range_proof_instruction_offset == 0`: `has_sysvar` is true, so `instructionsSysvar` consumes slot 2; the subsequent range check (`range_offset == 0 && offset < 3`) is false, so `rangeProofContextStateAccount` is dropped entirely, and index 3 (which the real protocol semantics require to be the range-proof context account, since a real 5-account layout would be `account, mint, sysvar, rangeCtx, owner`) is instead labeled `"owner"`.
- `equality_proof_instruction_offset == 0`, `range_proof_instruction_offset != 0`: symmetric case — `equalityProofContextStateAccount` is dropped and index 3 is mislabeled `"owner"`.
- Both offsets `== 0`: `equalityProofContextStateAccount` consumes slot 2; the range check fails, `rangeProofContextStateAccount` is dropped, and index 3 is mislabeled `"owner"`.

Only the case where both offsets are non-zero (`account,mint,sysvar,owner` = 4 accounts) matches the minimal account count correctly.

This code path is reached purely through `transaction-status` parsing logic invoked whenever a client requests `jsonParsed` encoding for a transaction/instruction (e.g., `getTransaction`) — it operates on raw `CompiledInstruction` data and account indexes as submitted on-chain, independent of whether the instruction actually succeeded at runtime. An attacker only needs the transaction to land in a block (it will fail token-2022 program validation for insufficient accounts, but a failed, fee-paying transaction is still recorded and retrievable). No signature, keys, or privileged role beyond being a fee-paying transaction sender are required.

The bounds themselves are safe (no panic): `account_indexes[offset]` is always checked against `account_indexes.len()`, and `account_keys[...]` is bounded by the caller's `instruction.accounts.iter().max() < account_keys.len()` check in `parse_token`. The bug is a semantic/fidelity issue, not memory-safety.

### Impact Explanation
This matches the "PARSE_FIDELITY" scoped impact category the question describes: an unprivileged attacker can cause a downstream RPC/integrator client to receive JSON output where the `"owner"` field of a `withdrawConfidentialTransfer` parsed instruction is actually a range/equality proof context-state account key, not the true authority. Any integrator relying on parsed `owner`/`multisigOwner` fields for authorization display, auditing, or indexing would be misled about account roles. No panic, no consensus impact, no direct fund loss — the impact is confined to misreported decoded metadata returned by RPC.

### Likelihood Explanation
Fully attacker-controlled and cheaply repeatable: the attacker only needs to craft and submit (or even just have processed as a failed transaction) a Token-2022 Confidential Transfer `Withdraw` instruction with `account_indexes.len() == 4` and one of the three vulnerable offset combinations (`eq != 0 && range == 0`, `eq == 0 && range != 0`, or `eq == 0 && range == 0` with insufficient accounts), then query it via a single `getTransaction`/`getBlock` RPC call with `jsonParsed` encoding. This requires no elevated privileges, no more than one RPC call, and is fully deterministic/reproducible.

### Recommendation
Compute the exact number of accounts required from the specific combination of `equality_proof_instruction_offset` and `range_proof_instruction_offset` (mirroring how `spl_token_2022_interface`'s `inner_withdraw` builds the account list) and validate `account_indexes.len()` against that exact count before assigning slots, rather than only enforcing a generic minimum of 4 via `check_num_token_accounts`. If the account count doesn't match what the offsets imply, return `ParseInstructionError::InstructionNotParsable` instead of guessing and mislabeling. Apply the analogous fix to the structurally identical `Transfer`/`TransferWithFee` branches, which use the same pattern.

### Proof of Concept
```rust
// transaction-status/src/parse_token/extension/confidential_transfer.rs (test module)
#[test]
fn test_withdraw_insufficient_accounts_mislabels_owner() {
    use spl_token_2022_interface::extension::confidential_transfer::instruction::WithdrawInstructionData;

    // Simulate: eq_offset != 0 (sysvar-based), range_offset == 0 (context-account-based),
    // but attacker supplies only 4 accounts (account, mint, sysvar, <range_ctx OR owner?>)
    let account = Pubkey::new_unique();
    let mint = Pubkey::new_unique();
    let sysvar_or_range_ctx = Pubkey::new_unique(); // ambiguous: real layout needs 5 accounts

    let withdrawal_data = WithdrawInstructionData {
        amount: 42.into(),
        decimals: 9,
        new_decryptable_available_balance: Default::default(),
        equality_proof_instruction_offset: 1,  // non-zero -> sysvar expected
        range_proof_instruction_offset: 0,      // zero -> context account expected
    };
    let instruction_data = /* encode withdrawal_data with discriminator */;
    let account_indexes: Vec<u8> = vec![0, 1, 2, 3]; // len == 4, minimum only

    let account_keys = AccountKeys::new(&[account, mint, sysvar_or_range_ctx, /* 4th key */ Pubkey::new_unique()], None);

    let result = parse_confidential_transfer_instruction(&instruction_data, &account_indexes, &account_keys).unwrap();

    // BUG: rangeProofContextStateAccount is silently dropped
    assert!(result.info.get("rangeProofContextStateAccount").is_none());
    // BUG: index 3 (which should be the range-proof context account per real 5-account layout)
    // is mislabeled as "owner"
    assert_eq!(result.info["owner"], json!(account_keys[3].to_string()));
    // Expected (fixed) behavior: this combination with only 4 accounts should be rejected
    // as InstructionNotParsable, not silently mislabeled.
}
```
Extend this into an exhaustive test over `{eq_offset, range_offset} x {0, nonzero}` combined with `account_indexes.len()` in `4..=6`, asserting either (a) a parse error when the account count doesn't match what the offsets require, or (b) that every populated field key (`instructionsSysvar`, `equalityProofContextStateAccount`, `rangeProofContextStateAccount`, `owner`) maps to the semantically correct account index — never silently dropping a context-account field while mislabeling that same index as `owner`.

### Citations

**File:** transaction-status/src/parse_token/extension/confidential_transfer.rs (L177-226)
```rust
        ConfidentialTransferInstruction::Withdraw => {
            check_num_token_accounts(account_indexes, 4)?;
            let withdrawal_data: WithdrawInstructionData =
                *decode_instruction_data(instruction_data).map_err(|_| {
                    ParseInstructionError::InstructionNotParsable(ParsableProgram::SplToken)
                })?;
            let amount: u64 = withdrawal_data.amount.into();
            let mut value = json!({
                "account": account_keys[account_indexes[0] as usize].to_string(),
                "mint": account_keys[account_indexes[1] as usize].to_string(),
                "amount": amount,
                "decimals": withdrawal_data.decimals,
                "newDecryptableAvailableBalance": format!("{}", withdrawal_data.new_decryptable_available_balance),
                "equalityProofInstructionOffset": withdrawal_data.equality_proof_instruction_offset,
                "rangeProofInstructionOffset": withdrawal_data.range_proof_instruction_offset,

            });

            let mut offset = 2;
            let map = value.as_object_mut().unwrap();
            let has_sysvar = withdrawal_data.equality_proof_instruction_offset != 0
                || withdrawal_data.range_proof_instruction_offset != 0;

            if has_sysvar && offset < account_indexes.len().saturating_sub(1) {
                map.insert(
                    "instructionsSysvar".to_string(),
                    json!(account_keys[account_indexes[offset] as usize].to_string()),
                );
                offset += 1;
            }

            if withdrawal_data.equality_proof_instruction_offset == 0
                && offset < account_indexes.len().saturating_sub(1)
            {
                map.insert(
                    "equalityProofContextStateAccount".to_string(),
                    json!(account_keys[account_indexes[offset] as usize].to_string()),
                );
                offset += 1;
            }

            if withdrawal_data.range_proof_instruction_offset == 0
                && offset < account_indexes.len().saturating_sub(1)
            {
                map.insert(
                    "rangeProofContextStateAccount".to_string(),
                    json!(account_keys[account_indexes[offset] as usize].to_string()),
                );
                offset += 1;
            }
```
