### Title
Withdraw proof-context accounts silently mislabeled as `owner` when account count doesn't match proof-offset flags - (transaction-status/src/parse_token/extension/confidential_transfer.rs)

### Summary
In `parse_confidential_transfer_instruction`'s `ConfidentialTransferInstruction::Withdraw` branch, the code advances an `offset` counter based on which proof fields require context-state accounts, but only bounds-checks `offset` against `account_indexes.len() - 1` rather than validating that the account list actually contains the number of accounts implied by the declared proof-offset flags. When an attacker crafts a Withdraw instruction whose `account_indexes` list is exactly at the minimum length required by `check_num_token_accounts(4)` but the `equality_proof_instruction_offset`/`range_proof_instruction_offset` flags indicate that *two* context-state accounts should be present, the parser silently drops the `rangeProofContextStateAccount` field and instead passes the same account index into `parse_signers`, mislabeling a proof-context account as `"owner"`.

### Finding Description
The Withdraw branch computes: [1](#0-0) 

Starting at `offset = 2`, the code conditionally consumes account slots for `instructionsSysvar`, `equalityProofContextStateAccount`, and `rangeProofContextStateAccount`, guarding each consumption with `offset < account_indexes.len().saturating_sub(1)`. This check only prevents `account_indexes` out-of-bounds indexing (no panic risk, since `account_indexes` values are already validated by transaction sanitization to be valid indices into `account_keys`) — but it does **not** verify that the number of accounts actually supplied matches what the `equality_proof_instruction_offset == 0` / `range_proof_instruction_offset == 0` flags claim.

Concretely, with `account_indexes.len() == 4` (the boundary allowed by `check_num_token_accounts(account_indexes, 4)`) and both `equality_proof_instruction_offset == 0` and `range_proof_instruction_offset == 0` (meaning both proofs are supposed to live in separate context-state accounts, requiring 5 accounts total: account, mint, eq_ctx, range_ctx, owner):
- `has_sysvar` is `false` (both offsets are 0), so the `instructionsSysvar` branch is skipped.
- The equality check (`offset=2 < 3`) succeeds, inserting `equalityProofContextStateAccount = account_indexes[2]`, `offset` becomes 3.
- The range check (`offset=3 < 3`) fails, so `rangeProofContextStateAccount` is silently omitted.
- `parse_signers(map, offset=3, ...)` is then called, which — because `account_indexes.len() (4) > last_nonsigner_index+1 (4)` is false — takes the else branch and labels `account_indexes[3]` (the account that was actually meant to be `rangeProofContextStateAccount`) as `"owner"`.

The result is a syntactically valid, non-panicking parse result that mislabels a proof-context account as the transaction `owner`/authority field, without any error or warning that the account count doesn't match the declared proof layout.

### Impact Explanation
This is decoder misreporting of account attribution in `jsonParsed` RPC output (`getTransaction`/`getParsedTransaction`), falling under the "wrong-slot/fork/account data returned … or decoder panic and misreporting" category. A wallet, explorer, or automated tool relying on the parsed `owner` field for a confidential-transfer withdrawal could attribute authority/ownership to the wrong account (an unrelated proof-context account), which is a real security-relevant misreporting bug even though it doesn't cause a crash. Scoped impact is limited to display/parsing metadata; it does not affect actual on-chain execution or consensus state.

### Likelihood Explanation
The attacker only needs to submit an ordinary transaction (unprivileged, single client, one construction) containing a `spl-token-2022` `Withdraw` confidential-transfer instruction where the number of accounts passed does not match what `equality_proof_instruction_offset`/`range_proof_instruction_offset` imply (e.g., 4 accounts with both offsets `== 0`). The transaction need not succeed on-chain — even a failed transaction is recorded and can subsequently be retrieved via a single `getTransaction`/`getParsedTransaction` RPC call with `jsonParsed` encoding, which invokes this exact parser code path. This makes the issue trivially reproducible with a single crafted instruction and a single RPC call, matching the allowed attacker capability.

### Recommendation
Instead of only bounds-checking `offset` against `account_indexes.len() - 1`, explicitly compute the expected number of accounts from the proof-offset flags (`instructionsSysvar` if any offset != 0, plus one per proof offset == 0) and compare against `account_indexes.len()` before assigning any labels. If the actual count is insufficient, return a `ParseInstructionError::InstructionNotParsable` rather than silently omitting fields and letting `parse_signers` reinterpret leftover proof-context accounts as `owner`/`multisigOwner`. Apply the same fix to the analogous `Transfer` and `TransferWithFee` branches, which share the identical offset-accumulation pattern.

### Proof of Concept
```rust
// transaction-status/src/parse_token/extension/confidential_transfer.rs (test module)
#[test]
fn test_withdraw_mislabels_range_ctx_as_owner_when_accounts_insufficient() {
    let token_account = Pubkey::new_unique();
    let mint = Pubkey::new_unique();
    let eq_ctx = Pubkey::new_unique(); // actually equalityProofContextStateAccount
    let range_ctx = Pubkey::new_unique(); // actually rangeProofContextStateAccount, but will be
                                           // mislabeled as "owner"

    // Manually construct a Withdraw instruction with both proof offsets == 0
    // (ContextStateAccount for both) but supply only 4 accounts total instead
    // of the required 5 (account, mint, eq_ctx, range_ctx, owner).
    let withdrawal_data = WithdrawInstructionData {
        amount: 42.into(),
        decimals: 9,
        new_decryptable_available_balance: PodAeCiphertext::default(),
        equality_proof_instruction_offset: 0,
        range_proof_instruction_offset: 0,
    };
    let mut data = vec![/* ConfidentialTransferInstruction::Withdraw discriminant bytes */];
    data.extend_from_slice(bytemuck::bytes_of(&withdrawal_data));

    let accounts = vec![
        AccountMeta::new(token_account, false),
        AccountMeta::new_readonly(mint, false),
        AccountMeta::new_readonly(eq_ctx, false),
        AccountMeta::new_readonly(range_ctx, false), // should be range_ctx, gets labeled "owner"
    ];
    let instruction = Instruction {
        program_id: spl_token_2022_interface::id(),
        accounts,
        data,
    };

    let message = Message::new(&[instruction], None);
    let parsed = parse_token(
        &message.instructions[0],
        &AccountKeys::new(&message.account_keys, None),
    )
    .unwrap();

    // Expected (safe) behavior: parser should error out or clearly flag insufficient accounts.
    // Actual (buggy) behavior asserted here:
    assert!(parsed.info.get("rangeProofContextStateAccount").is_none());
    assert_eq!(parsed.info["owner"], json!(range_ctx.to_string())); // misattribution
}
```
Expected assertion for the fix: this test should instead fail to parse (`ParseInstructionError::InstructionNotParsable`) rather than emit a plausible-looking but incorrect `owner` field equal to `range_ctx`.

### Citations

**File:** transaction-status/src/parse_token/extension/confidential_transfer.rs (L195-226)
```rust
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
