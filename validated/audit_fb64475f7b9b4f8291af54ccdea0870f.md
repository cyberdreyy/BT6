### Title
`WithdrawWithheldTokensFromAccounts` parsing mislabels source token accounts as withdraw authority/signers due to attacker-controlled `num_token_accounts` - (File: transaction-status/src/parse_token/extension/transfer_fee.rs)

### Summary
`parse_transfer_fee_instruction` (and its confidential-transfer-fee analogue) trusts the `num_token_accounts` field embedded in `WithdrawWithheldTokensFromAccounts` instruction data to split `account_indexes` into an authority/signer region and a source-account region, but only enforces a lower-bound length check via `check_num_token_accounts`, not that the split boundary is consistent with how the instruction was actually built. An attacker can set `num_token_accounts` to any value satisfying `account_indexes.len() >= 3 + num_token_accounts` and thereby move real source token accounts into the authority/multisig-signer role (or vice versa) in the JSON returned by `getTransaction`/`getConfirmedTransaction` with `jsonParsed` encoding.

### Finding Description
In `transaction-status/src/parse_token/extension/transfer_fee.rs`: [1](#0-0) 
the code does:
```
TransferFeeInstruction::WithdrawWithheldTokensFromAccounts { num_token_accounts } => {
    check_num_token_accounts(account_indexes, 3 + num_token_accounts as usize)?;
    ...
    let first_source_account_index = account_indexes.len().saturating_sub(num_token_accounts as usize);
    for i in account_indexes[first_source_account_index..].iter() { source_accounts.push(...) }
    ...
    parse_signers(map, 2, account_keys, &account_indexes[..first_source_account_index], "withdrawWithheldAuthority", "multisigWithdrawWithheldAuthority");
}
```
`check_num_token_accounts` (used identically across the file, e.g. also seen guarding `TransferCheckedWithFee`/other variants) only verifies a *minimum* total account count; it never checks that `num_token_accounts` equals the number of trailing accounts that were genuinely intended as sources versus the leading authority/multisig region. `num_token_accounts` is fully attacker-controlled instruction data — it comes straight from the `TokenInstruction::unpack` decode of raw instruction bytes that any client can submit in a transaction, regardless of whether the transaction later succeeds at runtime. Since `getTransaction`/`getConfirmedTransaction` (jsonParsed) parse committed instruction data independent of the SPL Token-2022 program's own runtime validation of the same field, an attacker can craft a transaction whose instruction data disagrees with what the accounts list "should" mean:

- Setting `num_token_accounts` smaller than the real trailing-source count increases `first_source_account_index`, pulling genuine source token accounts into the slice passed to `parse_signers`, which then labels them `withdrawWithheldAuthority`/`multisigWithdrawWithheldAuthority`/`signers`.
- Setting `num_token_accounts` larger (still satisfying the `>= 3+n` bound, e.g. by padding with extra dummy account keys) decreases `first_source_account_index`, pulling what were meant to be authority/multisig-signer accounts into `sourceAccounts`.

The identical pattern (same missing exact-split validation) exists in the confidential-transfer-fee variant. [2](#0-1) 

The lower-bound check does prevent out-of-bounds panics: because `num_token_accounts <= account_indexes.len() - 3` (or `-4` for the confidential variant), `first_source_account_index` is always `>= 3` (or `>=4`), so `parse_signers` never indexes past the front of the slice. So this is not a crash/panic bug — it is a pure data-mislabeling (misreporting) bug in the parsed JSON output.

### Impact Explanation
This is a decoder misreporting issue: RPC clients (explorers, wallets, indexers) calling `getTransaction`/`getConfirmedTransaction` with `encoding: jsonParsed` on a transaction containing a crafted `WithdrawWithheldTokensFromAccounts` (or `WithdrawWithheldTokensFromMint`-adjacent `WithdrawWithheldTokensFromAccounts` confidential variant) instruction receive JSON where ordinary token accounts are labeled `withdrawWithheldAuthority`/`multisigWithdrawWithheldAuthority`/`signers`, or conversely signer/authority accounts are labeled as `sourceAccounts`. This can mislead downstream consumers about which accounts authorized a withheld-fee withdrawal, a decoder misreporting issue as explicitly listed as an acceptable impact category. It does not cause a validator crash, consensus-state mutation, or unbounded cost, so its severity is bounded to RPC data-integrity misreporting.

### Likelihood Explanation
Fully exploitable by an unprivileged attacker with a single signed transaction submitted via `sendTransaction`/`simulateTransaction`, requiring no special privileges, staked node, or leader control. The instruction need not even succeed at runtime (the ledger stores instruction data for both successful and failed transactions), so an attacker only needs the transaction to land in a block — trivially achievable by paying a normal fee. This is deterministic and repeatable for any attacker-chosen account list/`num_token_accounts` combination that satisfies the loose lower-bound check.

### Recommendation
Change `check_num_token_accounts` calls for `WithdrawWithheldTokensFromAccounts` (both the transfer-fee and confidential-transfer-fee variants) to validate the exact expected account count rather than a lower bound, i.e. require `account_indexes.len() == 3 + num_token_accounts` (or `4 + num_token_accounts`), returning `ParseInstructionError::InstructionNotParsable` when the counts don't match exactly, mirroring the exact validation the SPL Token-2022 program itself performs at execution time.

### Proof of Concept
```rust
// transaction-status/src/parse_token/extension/transfer_fee.rs (test)
#[test]
fn test_withdraw_withheld_tokens_from_accounts_num_token_accounts_mismatch() {
    use spl_token_2022_interface::extension::transfer_fee::instruction::withdraw_withheld_tokens_from_accounts;

    let mint = Pubkey::new_unique();
    let recipient = Pubkey::new_unique();
    let authority = Pubkey::new_unique();
    let source_1 = Pubkey::new_unique();
    let source_2 = Pubkey::new_unique();

    // Build instruction normally with 2 source accounts (num_token_accounts = 2),
    // then tamper with the encoded num_token_accounts byte to claim only 0 sources,
    // while leaving the account list (accounts.len()) unchanged.
    let mut ix = withdraw_withheld_tokens_from_accounts(
        &spl_token_2022_interface::id(),
        &mint,
        &recipient,
        &authority,
        &[],
        &[&source_1, &source_2],
    )
    .unwrap();

    // Instruction data layout: [0]=TransferFeeExtension tag, [1]=WithdrawWithheldTokensFromAccounts tag,
    // [2]=num_token_accounts (u8). Overwrite to 0.
    let num_token_accounts_offset = 2;
    ix.data[num_token_accounts_offset] = 0;

    let message = Message::new(&[ix], None);
    let compiled_instruction = &message.instructions[0];
    let parsed = parse_token(
        compiled_instruction,
        &AccountKeys::new(&message.account_keys, None),
    )
    .unwrap();

    // Expected/safe behavior: either a ParseInstructionError, or sourceAccounts still
    // correctly lists source_1/source_2 and withdrawWithheldAuthority is `authority`.
    // Actual (buggy) behavior: sourceAccounts is empty and source_1/source_2 get folded
    // into the multisig "signers" list under a mislabeled "multisigWithdrawWithheldAuthority".
    assert_eq!(
        parsed.info["sourceAccounts"],
        json!(vec![source_1.to_string(), source_2.to_string()]),
        "source accounts must not be reassigned to signer/authority role when num_token_accounts is tampered"
    );
    assert_eq!(
        parsed.info["withdrawWithheldAuthority"],
        json!(authority.to_string()),
    );
}
```
Expected result on the current code: the assertions fail — `sourceAccounts` is empty/incorrect and `source_1`/`source_2` appear under `signers`/`multisigWithdrawWithheldAuthority` instead, confirming the mislabeling described above.

### Citations

**File:** transaction-status/src/parse_token/extension/transfer_fee.rs (L92-118)
```rust
        TransferFeeInstruction::WithdrawWithheldTokensFromAccounts { num_token_accounts } => {
            check_num_token_accounts(account_indexes, 3 + num_token_accounts as usize)?;
            let mut value = json!({
                "mint": account_keys[account_indexes[0] as usize].to_string(),
                "feeRecipient": account_keys[account_indexes[1] as usize].to_string(),
            });
            let map = value.as_object_mut().unwrap();
            let mut source_accounts: Vec<String> = vec![];
            let first_source_account_index = account_indexes
                .len()
                .saturating_sub(num_token_accounts as usize);
            for i in account_indexes[first_source_account_index..].iter() {
                source_accounts.push(account_keys[*i as usize].to_string());
            }
            map.insert("sourceAccounts".to_string(), json!(source_accounts));
            parse_signers(
                map,
                2,
                account_keys,
                &account_indexes[..first_source_account_index],
                "withdrawWithheldAuthority",
                "multisigWithdrawWithheldAuthority",
            );
            Ok(ParsedInstructionEnum {
                instruction_type: "withdrawWithheldTokensFromAccounts".to_string(),
                info: value,
            })
```

**File:** transaction-status/src/parse_token/extension/confidential_transfer_fee.rs (L85-121)
```rust
            let num_token_accounts = withdraw_withheld_data.num_token_accounts;
            check_num_token_accounts(account_indexes, 4 + num_token_accounts as usize)?;
            let proof_instruction_offset: i8 = withdraw_withheld_data.proof_instruction_offset;
            let mut value = json!({
                "mint": account_keys[account_indexes[0] as usize].to_string(),
                "feeRecipient": account_keys[account_indexes[1] as usize].to_string(),
                "proofInstructionOffset": proof_instruction_offset,
                "newDecryptableAvailableBalance": format!("{}", withdraw_withheld_data.new_decryptable_available_balance),
            });
            let map = value.as_object_mut().unwrap();
            let first_source_account_index = account_indexes
                .len()
                .saturating_sub(num_token_accounts as usize);
            if proof_instruction_offset == 0 {
                map.insert(
                    "proofContextStateAccount".to_string(),
                    json!(account_keys[account_indexes[2] as usize].to_string()),
                );
            } else {
                map.insert(
                    "instructionsSysvar".to_string(),
                    json!(account_keys[account_indexes[2] as usize].to_string()),
                );
            }
            let mut source_accounts: Vec<String> = vec![];
            for i in account_indexes[first_source_account_index..].iter() {
                source_accounts.push(account_keys[*i as usize].to_string());
            }
            map.insert("sourceAccounts".to_string(), json!(source_accounts));
            parse_signers(
                map,
                3,
                account_keys,
                &account_indexes[..first_source_account_index],
                "withdrawWithheldAuthority",
                "multisigWithdrawWithheldAuthority",
            );
```
