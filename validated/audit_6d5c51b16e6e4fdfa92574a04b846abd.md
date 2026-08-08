### Title
Off-by-one in ConfidentialTransfer `Withdraw`/`Transfer` parsing causes account-label misattribution at trailing-account-count boundaries - ([File: transaction-status/src/parse_token/extension/confidential_transfer.rs])

### Summary
`parse_confidential_transfer_instruction` decodes `Withdraw`/`Transfer`/`TransferWithFee` instructions by walking an `offset` cursor over `account_indexes` and inserting optional fields (`instructionsSysvar`, `equalityProofContextStateAccount`, etc.) guarded only by `offset < account_indexes.len().saturating_sub(1)`. This guard is a pure bounds check reserving exactly one trailing slot for `parse_signers`, but it does not verify that the number of accounts actually present matches the number of optional accounts implied by the instruction data's proof-offset fields (`equality_proof_instruction_offset`, `range_proof_instruction_offset`, etc.), which are also fully attacker-controlled. Consequently, an attacker who submits a `CompiledInstruction` whose account list is shorter than what the decoded flags claim (or exactly at the boundary) causes the parser to attribute the wrong account key to `instructionsSysvar`/`...ContextStateAccount` fields, or to the `owner`/`multisigOwner` field, without returning an `Err`.

### Finding Description
For `ConfidentialTransferInstruction::Withdraw` [1](#0-0) , only `check_num_token_accounts(account_indexes, 4)` is enforced — a minimum-length check, not an exact-length check tied to the decoded proof-offset flags. The subsequent optional-field logic uses `offset < account_indexes.len().saturating_sub(1)` purely to avoid an index-out-of-bounds panic [2](#0-1) , then finally calls `parse_signers(map, offset, ...)` which treats `account_indexes[offset]` as `owner` (or the last signer before multisig) [3](#0-2) .

Because `has_sysvar`/`equality_proof_instruction_offset == 0` are derived entirely from attacker-supplied instruction data, and the account count is independently attacker-supplied, an attacker can set `equality_proof_instruction_offset != 0` (so `has_sysvar` is true) while only including the minimum 4 accounts (`account`, `mint`, then one more before the trailing owner slot). With `account_indexes.len() == 4`, `len().saturating_sub(1) == 3`; `offset` starts at 2, so `2 < 3` is true and the account at index 2 — which in a well-formed instruction would be the `owner` — is instead labeled `instructionsSysvar`. `offset` becomes 3 and is then fed to `parse_signers`, which reads `account_indexes[3]` (whatever trailing account is present) as `owner`. No bounds check catches this because the guard only prevents panics, not semantic mismatch, and `decode_instruction_data` does not cross-validate offset values against the account list length. The same pattern repeats for `Transfer` (offset starts at 3) [4](#0-3)  and `TransferWithFee` (offset starts at 3, five optional fields) [5](#0-4) .

The existing unit tests only exercise well-formed instructions built via the SPL Token-2022 SDK helper functions (`inner_empty_account`, etc.) where the account list always matches the encoded offsets [6](#0-5) ; they do not cover the case of a mismatched/malicious account count, so the misattribution path is untested and reachable.

### Impact Explanation
This is a decoder correctness/misreporting bug reachable via a single `getTransaction` (jsonParsed) RPC call on an attacker-crafted transaction: the parsed JSON can report the wrong account as `instructionsSysvar`, `equalityProofContextStateAccount`, `rangeProofContextStateAccount`, or `owner`/`multisigOwner`. This falls under the Validate section's accepted category of "decoder panic and misreporting." Note, however, that because the attacker fully controls both the account list and the instruction data of their own instruction, the mislabeling only affects the informational/explorer-facing representation of the attacker's own transaction — it does not alter on-chain execution, consensus state, or affect any other party's funds/authority, and a mismatched account count would generally cause the real SPL Token-2022 program to reject the instruction at runtime anyway. The impact is therefore limited to inaccurate `jsonParsed` output for such (likely failing) transactions.

### Likelihood Explanation
High feasibility to trigger the code path: the attacker only needs to submit a syntactically valid `CompiledInstruction` with the SPL-Token-2022 program id, the `ConfidentialTransferInstruction` discriminant plus `Withdraw`/`Transfer` sub-discriminant, arbitrary/decodable fixed-size instruction data (proof-offset fields are `i8`, easily set nonzero), and a variable-length trailing account list at or near the minimum enforced by `check_num_token_accounts`. No signature or execution success is required to reach the parser, since `getTransaction` parses whatever `CompiledInstruction` was included in a confirmed transaction, and the parser makes no attempt to verify the accounts actually satisfy the flags encoded in the data.

### Recommendation
Replace the length-only `saturating_sub(1)` bound checks with an exact expected-account-count computation derived from the decoded proof-offset flags (count how many of `instructionsSysvar` + each `...ContextStateAccount` should be present, then require `account_indexes.len() == base_accounts + expected_optional_accounts + 1 (owner) [+ signers]`), returning `ParseInstructionError::InstructionNotParsable` when the actual count doesn't match the expected count for the encoded flags, mirroring how `check_num_token_accounts` already enforces a floor. This closes the gap between "no panic" and "no misattribution."

### Proof of Concept
Rust integration test sketch (add to `transaction-status/src/parse_token/extension/confidential_transfer.rs` tests):
```rust
#[test]
fn test_withdraw_account_count_boundary_misattribution() {
    use solana_instruction::{AccountMeta, Instruction};
    use solana_message::Message;

    let token_account = Pubkey::new_unique();
    let mint = Pubkey::new_unique();
    let real_owner = Pubkey::new_unique(); // should be reported as "owner"

    // Manually build a Withdraw instruction with equality_proof_instruction_offset != 0
    // (has_sysvar == true) but only the minimum 4 accounts: account, mint, real_owner, extra.
    let mut data = vec![/* ConfidentialTransferExtension tag, Withdraw sub-tag */];
    // ... append WithdrawInstructionData with amount=0, decimals=0,
    // new_decryptable_available_balance=default, equality_proof_instruction_offset=1,
    // range_proof_instruction_offset=0

    let accounts = vec![
        AccountMeta::new(token_account, false),
        AccountMeta::new_readonly(mint, false),
        AccountMeta::new_readonly(real_owner, true), // intended "owner"
        AccountMeta::new_readonly(Pubkey::new_unique(), true), // trailing slot for parse_signers
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

    // Expected (per WithdrawInstructionData semantics): "owner" == real_owner.
    // Actual: "instructionsSysvar" incorrectly consumes real_owner's slot,
    // and "owner" is misattributed to the next trailing account instead.
    assert_ne!(
        parsed.info.get("instructionsSysvar"),
        None,
        "demonstrates real_owner slot consumed as instructionsSysvar"
    );
    assert_ne!(
        parsed.info["owner"], json!(real_owner.to_string()),
        "owner misattributed due to off-by-one boundary logic"
    );
}
```
Expected result: the test demonstrates that `real_owner` (intended to be labeled `owner`) is instead labeled `instructionsSysvar`, and a different (unintended) account ends up labeled `owner`, confirming the off-by-one misattribution described. A broader fuzz/differential test sweeping account-list lengths 3..8 against all combinations of proof-offset flags for `Withdraw`, `Transfer`, and `TransferWithFee` would systematically enumerate all boundary mismatches.

### Citations

**File:** transaction-status/src/parse_token/extension/confidential_transfer.rs (L177-198)
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
```

**File:** transaction-status/src/parse_token/extension/confidential_transfer.rs (L200-226)
```rust
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

**File:** transaction-status/src/parse_token/extension/confidential_transfer.rs (L241-299)
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
                "newSourceDecryptableAvailableBalance": format!("{}", transfer_data.new_source_decryptable_available_balance),
                "equalityProofInstructionOffset": transfer_data.equality_proof_instruction_offset,
                "ciphertextValidityProofInstructionOffset": transfer_data.ciphertext_validity_proof_instruction_offset,
                "rangeProofInstructionOffset": transfer_data.range_proof_instruction_offset,

            });
            let mut offset = 3;
            let map = value.as_object_mut().unwrap();
            let has_sysvar = transfer_data.equality_proof_instruction_offset != 0
                || transfer_data.ciphertext_validity_proof_instruction_offset != 0
                || transfer_data.range_proof_instruction_offset != 0;

            if has_sysvar && offset < account_indexes.len().saturating_sub(1) {
                map.insert(
                    "instructionsSysvar".to_string(),
                    json!(account_keys[account_indexes[offset] as usize].to_string()),
                );
                offset += 1;
            }

            if transfer_data.equality_proof_instruction_offset == 0
                && offset < account_indexes.len().saturating_sub(1)
            {
                map.insert(
                    "equalityProofContextStateAccount".to_string(),
                    json!(account_keys[account_indexes[offset] as usize].to_string()),
                );
                offset += 1;
            }

            if transfer_data.ciphertext_validity_proof_instruction_offset == 0
                && offset < account_indexes.len().saturating_sub(1)
            {
                map.insert(
                    "ciphertextValidityProofContextStateAccount".to_string(),
                    json!(account_keys[account_indexes[offset] as usize].to_string()),
                );
                offset += 1;
            }

            if transfer_data.range_proof_instruction_offset == 0
                && offset < account_indexes.len().saturating_sub(1)
            {
                map.insert(
                    "rangeProofContextStateAccount".to_string(),
                    json!(account_keys[account_indexes[offset] as usize].to_string()),
                );
                offset += 1;
            }
```

**File:** transaction-status/src/parse_token/extension/confidential_transfer.rs (L314-401)
```rust
        ConfidentialTransferInstruction::TransferWithFee => {
            check_num_token_accounts(account_indexes, 4)?;
            let transfer_data: TransferWithFeeInstructionData =
                *decode_instruction_data(instruction_data).map_err(|_| {
                    ParseInstructionError::InstructionNotParsable(ParsableProgram::SplToken)
                })?;
            let equality_proof_instruction_offset: i8 =
                transfer_data.equality_proof_instruction_offset;
            let transfer_amount_ciphertext_validity_proof_instruction_offset: i8 =
                transfer_data.transfer_amount_ciphertext_validity_proof_instruction_offset;
            let fee_sigma_proof_instruction_offset: i8 =
                transfer_data.fee_sigma_proof_instruction_offset;
            let fee_ciphertext_validity_proof_instruction_offset: i8 =
                transfer_data.fee_ciphertext_validity_proof_instruction_offset;
            let range_proof_instruction_offset: i8 = transfer_data.range_proof_instruction_offset;
            let mut value = json!({
                "source": account_keys[account_indexes[0] as usize].to_string(),
                "mint": account_keys[account_indexes[1] as usize].to_string(),
                "destination": account_keys[account_indexes[2] as usize].to_string(),
                "newSourceDecryptableAvailableBalance": format!("{}", transfer_data.new_source_decryptable_available_balance),
                "equalityProofInstructionOffset": equality_proof_instruction_offset,
                "transferAmountCiphertextValidityProofInstructionOffset": transfer_amount_ciphertext_validity_proof_instruction_offset,
                "feeCiphertextValidityProofInstructionOffset": fee_ciphertext_validity_proof_instruction_offset,
                "feeSigmaProofInstructionOffset": fee_sigma_proof_instruction_offset,
                "rangeProofInstructionOffset": range_proof_instruction_offset,
            });

            let mut offset = 3;
            let map = value.as_object_mut().unwrap();
            let has_sysvar = equality_proof_instruction_offset != 0
                || transfer_amount_ciphertext_validity_proof_instruction_offset != 0
                || fee_sigma_proof_instruction_offset != 0
                || fee_ciphertext_validity_proof_instruction_offset != 0
                || range_proof_instruction_offset != 0;

            if has_sysvar && offset < account_indexes.len().saturating_sub(1) {
                map.insert(
                    "instructionsSysvar".to_string(),
                    json!(account_keys[account_indexes[offset] as usize].to_string()),
                );
                offset += 1;
            }

            if equality_proof_instruction_offset == 0
                && offset < account_indexes.len().saturating_sub(1)
            {
                map.insert(
                    "equalityProofContextStateAccount".to_string(),
                    json!(account_keys[account_indexes[offset] as usize].to_string()),
                );
                offset += 1;
            }
            if transfer_amount_ciphertext_validity_proof_instruction_offset == 0
                && offset < account_indexes.len().saturating_sub(1)
            {
                map.insert(
                    "transferAmountCiphertextValidityProofContextStateAccount".to_string(),
                    json!(account_keys[account_indexes[offset] as usize].to_string()),
                );
                offset += 1;
            }
            if fee_sigma_proof_instruction_offset == 0
                && offset < account_indexes.len().saturating_sub(1)
            {
                map.insert(
                    "feeSigmaProofContextStateAccount".to_string(),
                    json!(account_keys[account_indexes[offset] as usize].to_string()),
                );
                offset += 1;
            }
            if fee_ciphertext_validity_proof_instruction_offset == 0
                && offset < account_indexes.len().saturating_sub(1)
            {
                map.insert(
                    "feeCiphertextValidityProofContextStateAccount".to_string(),
                    json!(account_keys[account_indexes[offset] as usize].to_string()),
                );
                offset += 1;
            }
            if range_proof_instruction_offset == 0
                && offset < account_indexes.len().saturating_sub(1)
            {
                map.insert(
                    "rangeProofContextStateAccount".to_string(),
                    json!(account_keys[account_indexes[offset] as usize].to_string()),
                );
                offset += 1;
            }
```

**File:** transaction-status/src/parse_token/extension/confidential_transfer.rs (L724-807)
```rust
    #[test]
    fn test_empty_account() {
        let token_account = Pubkey::new_unique();
        let authority = Pubkey::new_unique();
        let proof_ctx = Pubkey::new_unique();

        let offset_proof = ProofLocation::InstructionOffset(
            NonZero::new(1).unwrap(),
            &ZeroCiphertextProofData::zeroed(),
        );

        let context_proof = ProofLocation::ContextStateAccount(&proof_ctx);

        // We test both proof locations to ensure the parser's offset logic holds.
        // Array of cases: (Test Name, Proof Location, Expected Offset)
        let cases = vec![
            ("Context State Account", context_proof, 0),
            ("Instruction Offset", offset_proof, 1),
        ];

        for (name, proof_location, expected_offset) in cases {
            let instruction = inner_empty_account(
                &spl_token_2022_interface::id(),
                &token_account,
                &authority,
                &[],
                proof_location,
            )
            .unwrap();

            check_no_panic(instruction.clone());

            let message = Message::new(&[instruction], None);
            let parsed = parse_token(
                &message.instructions[0],
                &AccountKeys::new(&message.account_keys, None),
            )
            .unwrap();

            // Core Property Assertions
            assert_eq!(
                parsed.instruction_type, "emptyConfidentialTransferAccount",
                "Failed on: {name}",
            );
            assert_eq!(
                parsed.info["account"],
                json!(token_account.to_string()),
                "Failed on: {name}",
            );
            assert_eq!(
                parsed.info["owner"],
                json!(authority.to_string()),
                "Failed on: {name}",
            );

            // Expected Offset
            assert_eq!(
                parsed.info["proofInstructionOffset"],
                json!(expected_offset),
                "Failed on: {name}",
            );

            if expected_offset == 0 {
                assert_eq!(
                    parsed.info["proofContextStateAccount"],
                    json!(proof_ctx.to_string()),
                    "Proof Context mismatch on: {name}",
                );
                assert!(
                    parsed.info.get("instructionsSysvar").is_none(),
                    "Sysvar should not be present on: {name}",
                );
            } else {
                assert_eq!(
                    parsed.info["instructionsSysvar"],
                    json!(sysvar::instructions::id().to_string()),
                    "Sysvar mismatch on: {name}",
                );
                assert!(
                    parsed.info.get("proofContextStateAccount").is_none(),
                    "Proof Context should not be present on: {name}",
                );
            }
        }
```

**File:** transaction-status/src/parse_token.rs (L937-960)
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
```
