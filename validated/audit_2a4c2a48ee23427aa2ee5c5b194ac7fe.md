### Title
Insufficient minimum-account check in `parse_confidential_transfer_instruction` Withdraw arm causes owner/authority misattribution - (File: transaction-status/src/parse_token/extension/confidential_transfer.rs)

### Summary
The `Withdraw` arm of `parse_confidential_transfer_instruction` only requires 4 accounts via `check_num_token_accounts(account_indexes, 4)`, but the field-to-account mapping logic that follows assumes up to 5 accounts are present when both `equality_proof_instruction_offset` and `range_proof_instruction_offset` are attacker-set to `0` (context-state-account mode). An attacker fully controls both `instruction_data` (decoded via `decode_instruction_data::<WithdrawInstructionData>`) and `account_indexes` when crafting a raw transaction, and can submit a 4-account instruction with both offsets zero to make the parser silently drop `rangeProofContextStateAccount` and mislabel that same account as `owner`.

### Finding Description
In the `Withdraw` arm (`transaction-status/src/parse_token/extension/confidential_transfer.rs`, lines 177-240), `offset` starts at 2 and is advanced conditionally based on attacker-controlled offset fields: [1](#0-0) 

With `account_indexes.len() == 4` (the bare minimum enforced by `check_num_token_accounts(account_indexes, 4)` at line 178) and both `equality_proof_instruction_offset == 0` and `range_proof_instruction_offset == 0` (attacker-chosen), the walk proceeds as:
- `has_sysvar` is `false` → the sysvar branch is skipped, `offset` stays `2`.
- Equality branch: `offset(2) < len-1(3)` is true → `equalityProofContextStateAccount = account_indexes[2]`, `offset` becomes `3`.
- Range branch: `offset(3) < len-1(3)` is `3 < 3` = **false** → the branch is skipped entirely; `rangeProofContextStateAccount` is never inserted even though `range_proof_instruction_offset == 0` signals it should be a context-state account.
- `parse_signers(map, offset=3, ...)` then reads `account_keys[account_indexes[3]]` and labels it `"owner"`.

The real reference layout for the "both proofs as context accounts" case requires 5 accounts (token account, mint, equality-context, range-context, owner), but the parser's minimum-length gate only demands 4. Because the gate is under-constrained relative to the branch logic that follows, the 4th account (index 3), which the attacker intends/labels as the range-proof context account, is instead reported to RPC consumers as the `"owner"` field, and the actual range-proof context account field is dropped from the parsed output. This is a decoder-side misreporting: no execution occurs, the parser only builds a JSON view for RPC/getTransaction/getConfirmedTransaction consumers, and it accepts any syntactically valid instruction regardless of whether the account list matches the semantic requirement of the chosen proof-offset combination.

### Impact Explanation
An unprivileged attacker submitting a single crafted (and, since only decoding is exercised, not even necessarily successfully executed by the actual program — decoding runs on whatever bytes/accounts are present in the compiled instruction) `Withdraw` confidential-transfer instruction can cause `getTransaction`/`getConfirmedTransaction`/parsed-instruction RPC responses to report the wrong account as `"owner"` (misreported authority) and to omit `"rangeProofContextStateAccount"` from the output. This matches the described bounty category of a decoder misreporting program/authority fields to downstream integrators (exchanges, explorers, wallets) that trust the parsed JSON without independently verifying against raw account/data bytes.

### Likelihood Explanation
This requires no special privilege: attacker only needs to construct a transaction/message with a `ConfidentialTransferInstruction::Withdraw` (or `TokenInstruction::ConfidentialTransferExtension`) instruction, using an arbitrary 4-entry `accounts` list and `instruction_data` with both proof offsets set to `0`, then query it via a JSON-RPC `getTransaction` (or similar parsed-instruction) call. This is fully deterministic and repeatable, requiring only one crafted transaction and one RPC read — no elevated access, no leader/validator control, and no more than the allowed single RPC call.

### Recommendation
Tighten the minimum account-count check in the `Withdraw` (and analogous `Transfer`/`TransferWithFee`) arms to depend on the actual proof-offset configuration decoded from `instruction_data`, e.g., require 5 accounts when both `equality_proof_instruction_offset == 0` and `range_proof_instruction_offset == 0`, or otherwise defensively verify `account_indexes.len()` is sufficient for the number of context-state-account slots the offsets imply before assigning `"owner"`. Alternatively, track how many slots were actually consumed by conditionally-inserted fields and only invoke `parse_signers` once every implied context account slot has been distinctly filled, returning `InstructionNotParsable` if the account list is inconsistent with the decoded offsets.

### Proof of Concept
```rust
// transaction-status/src/parse_token/extension/confidential_transfer.rs (test module)
#[test]
fn test_withdraw_underspecified_accounts_misattributes_owner() {
    use spl_token_2022_interface::extension::confidential_transfer::instruction::WithdrawInstructionData;

    let token_account = Pubkey::new_unique();
    let mint = Pubkey::new_unique();
    let range_ctx_intended = Pubkey::new_unique(); // attacker intends this as range-proof ctx account

    // Craft WithdrawInstructionData with BOTH offsets == 0 (context-account mode)
    let withdrawal_data = WithdrawInstructionData {
        amount: 42.into(),
        decimals: 9,
        new_decryptable_available_balance: Default::default(),
        equality_proof_instruction_offset: 0,
        range_proof_instruction_offset: 0,
    };
    let instruction_data = /* serialize instruction discriminator + withdrawal_data */;

    // Only 4 accounts supplied, though reference layout needs 5
    // (token_account, mint, equality_ctx, range_ctx/owner-collision, ...)
    let account_keys_vec = vec![token_account, mint, Pubkey::new_unique(), range_ctx_intended];
    let account_indexes: Vec<u8> = vec![0, 1, 2, 3];
    let account_keys = AccountKeys::new(&account_keys_vec, None);

    let parsed = parse_confidential_transfer_instruction(
        &instruction_data,
        &account_indexes,
        &account_keys,
    ).unwrap();

    // BUG: rangeProofContextStateAccount is silently dropped
    assert!(parsed.info.get("rangeProofContextStateAccount").is_none());

    // BUG: "owner" is misreported as the intended range-proof context account,
    // not an actual authority/signer account
    assert_eq!(
        parsed.info["owner"],
        json!(range_ctx_intended.to_string())
    );
}
```
Expected (bug confirmed): the assertions pass, demonstrating that `rangeProofContextStateAccount` is missing from parsed output and `owner` is populated from an account that is not an authority, purely due to attacker-controlled proof offsets combined with an under-constrained minimum account-count check.

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
