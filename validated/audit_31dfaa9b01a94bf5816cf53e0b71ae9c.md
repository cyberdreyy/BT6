### Title
Withdraw parser under-counts required accounts, causing the last context-state account to be mislabeled as `owner`/`multisigOwner` - (File: transaction-status/src/parse_token/extension/confidential_transfer.rs)

### Summary
`parse_confidential_transfer_instruction`'s `Withdraw` arm only validates `check_num_token_accounts(account_indexes, 4)` before running a sequential, `saturating_sub(1)`-guarded offset-advance algorithm that expects up to 4 optional trailing accounts (sysvar, equality-proof context, range-proof context) plus the final `owner`. When both `equality_proof_instruction_offset == 0` and `range_proof_instruction_offset == 0` (i.e., both proofs use context-state accounts), the real spl-token-2022 program semantics require 5 accounts (`account, mint, equality_ctx, range_ctx, owner`), but the parser's floor check only demands 4.

### Finding Description
In the `Withdraw` branch [1](#0-0) , the code walks `offset` starting at 2 and greedily assigns `instructionsSysvar`, then `equalityProofContextStateAccount`, then `rangeProofContextStateAccount`, each gated by `offset < account_indexes.len().saturating_sub(1)` to always reserve the final index for `owner`, which is then resolved via `parse_signers` [2](#0-1) .

For the "both proofs via context accounts" case (`eq_offset == 0 && range_offset == 0`), `has_sysvar` is `false`, so no sysvar slot is consumed, and two context accounts plus the owner are needed — 5 accounts total (verified against the exhaustive `test_withdraw` "All Contexts" case, which uses 5 accounts and assigns index 2→equality ctx, index 3→range ctx, index 4→owner) [3](#0-2) . However, the guard at the top of the arm only enforces a floor of 4 accounts via `check_num_token_accounts(account_indexes, 4)` [4](#0-3) .

If an attacker crafts a `Withdraw` instruction with exactly 4 account indices and both offsets set to 0, the parser's math becomes:
- `offset = 2`; `has_sysvar = false` → sysvar block skipped.
- `eq_offset == 0 && offset(2) < len(4).saturating_sub(1)=3` → true → `equalityProofContextStateAccount = account_indexes[2]`, `offset = 3`.
- `range_offset == 0 && offset(3) < 3` → false (3 is not < 3) → **skipped**, even though `rangeProofInstructionOffset` reported as `0` (implying a context account should exist).
- `parse_signers(map, offset=3, ...)` → `owner = account_indexes[3]`.

The account at `account_indexes[3]`, which per the real 5-account layout should be the `rangeProofContextStateAccount`, is instead labeled `owner`/`multisigOwner`. The JSON output simultaneously claims `"rangeProofInstructionOffset": 0` (context-account mode) while omitting the `rangeProofContextStateAccount` field and mislabeling that same account as the authority. On the real spl-token-2022 program, such a 4-account instruction with both offsets 0 would fail at execution (the program's own account iteration would run out of accounts before reaching a real owner), but the transaction is still included in a block (as a failed transaction) and its message/accounts are still returned by `getTransaction` with `jsonParsed` encoding, since this decoding path is independent of execution success and applies to any compiled instruction regardless of runtime outcome.

### Impact Explanation
This is a decoder misreporting bug: `getTransaction(jsonParsed)` for such a crafted (failing) Withdraw instruction returns an `owner` field pointing to an account that is not actually the withdrawal authority per program semantics, but rather the range-proof context-state account. This matches the "decoder ... misreporting" acceptance category. The impact is scoped to display/reporting correctness for a transaction that itself cannot succeed on-chain (since the real program would also run out of accounts), so there is no state-mutation or fund-movement impact — only misleading `jsonParsed` output for block explorers/monitoring tools that trust the parsed `owner` field.

### Likelihood Explanation
Fully attacker-controlled and reproducible with a single `sendTransaction` (even if it fails on-chain, it still lands in a block and is queryable) followed by a single `getTransaction` call, satisfying the "no more than one call per slot, single client" constraint. No validator/leader/peer control needed. The bug is deterministic given `account_indexes.len() == 4` with both proof offsets zero.

### Recommendation
In the `Withdraw` (and analogously `Transfer`/`TransferWithFee`) arms, replace the fixed floor `check_num_token_accounts(account_indexes, 4)` with a dynamically computed minimum based on `has_sysvar` plus the number of `== 0` offsets requiring context accounts (i.e., require `account_indexes.len() >= base_count + sysvar_needed + context_accounts_needed`) before running the offset-advance logic, and/or remove the `saturating_sub(1)` boundary shortcut in favor of counting required optional slots up front so the algorithm never silently drops a context-account assignment while still consuming that slot for `owner`.

### Proof of Concept
```rust
// transaction-status/src/parse_token/extension/confidential_transfer.rs (test module)
#[test]
fn test_withdraw_undersized_accounts_mislabels_owner() {
    let account = Pubkey::new_unique();
    let mint = Pubkey::new_unique();
    let would_be_range_ctx_but_labeled_owner = Pubkey::new_unique();

    // Craft raw instruction data manually: WithdrawInstructionData with
    // equality_proof_instruction_offset = 0, range_proof_instruction_offset = 0.
    let withdrawal_data = WithdrawInstructionData {
        amount: 42.into(),
        decimals: 9,
        new_decryptable_available_balance: PodAeCiphertext::default(),
        equality_proof_instruction_offset: 0,
        range_proof_instruction_offset: 0,
    };
    let mut data = vec![ConfidentialTransferInstruction::Withdraw as u8];
    data.extend_from_slice(bytemuck::bytes_of(&withdrawal_data));

    // Only 4 accounts, though "All Contexts" (both offsets 0) needs 5 per spec.
    let account_indexes: Vec<u8> = vec![0, 1, 2, 3];
    let account_keys = AccountKeys::new(
        &[account, mint, Pubkey::new_unique(), would_be_range_ctx_but_labeled_owner],
        None,
    );

    let parsed = parse_confidential_transfer_instruction(&data, &account_indexes, &account_keys)
        .unwrap();

    // Expected per spec: index 3 should be a context account, not "owner".
    // Actual: parser mislabels it as owner.
    assert_eq!(
        parsed.info["owner"],
        json!(would_be_range_ctx_but_labeled_owner.to_string()),
        "parser mislabels the range-proof context account as owner"
    );
    assert!(
        parsed.info.get("rangeProofContextStateAccount").is_none(),
        "range proof context account slot was silently dropped"
    );
}
```
Expected result: the assertions pass today, demonstrating that the account intended (per the 5-account "All Contexts" layout validated in the existing `test_withdraw` test) to be `rangeProofContextStateAccount` is instead reported as `owner`, confirming the misreporting.

### Citations

**File:** transaction-status/src/parse_token/extension/confidential_transfer.rs (L177-200)
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
```

**File:** transaction-status/src/parse_token/extension/confidential_transfer.rs (L208-235)
```rust
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

            parse_signers(
                map,
                offset,
                account_keys,
                account_indexes,
                "owner",
                "multisigOwner",
            );
```

**File:** transaction-status/src/parse_token/extension/confidential_transfer.rs (L877-877)
```rust
            ("All Contexts", context_eq, context_rng, 0, 0),
```
