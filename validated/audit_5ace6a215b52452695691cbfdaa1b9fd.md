### Title
Withdraw instruction parser silently drops proof-context accounts and mislabels them as `owner` when `account_indexes` is at the minimum-checked length - ([File: transaction-status/src/parse_token/extension/confidential_transfer.rs])

### Finding Description
`parse_confidential_transfer_instruction`'s `ConfidentialTransferInstruction::Withdraw` arm only requires `check_num_token_accounts(account_indexes, 4)` before doing sequential, offset-based indexing into `account_indexes`: [1](#0-0) 

Each conditional insertion is gated by `offset < account_indexes.len().saturating_sub(1)`, which is what actually prevents an out-of-bounds panic — since `check_num_token_accounts` guarantees `account_indexes.len() >= 4`, `saturating_sub(1)` never underflows and `offset` never reaches `account_indexes.len()`. So the literal "index-out-of-bounds panic" scenario in the question cannot occur; the guard is sound for panic-safety.

However, the guard is a fixed length-based cutoff, not a semantic count of how many "fixed" accounts the specific offset combination actually requires. The real spl-token-2022 Withdraw layout needs a variable number of leading accounts: an `instructionsSysvar` when *either* offset is nonzero, an `equalityProofContextStateAccount` when `equality_proof_instruction_offset == 0`, and a `rangeProofContextStateAccount` when `range_proof_instruction_offset == 0` — up to 3 extra accounts before the owner/signers. If an attacker crafts an instruction with `account_indexes.len() == 4` (the bare minimum that passes `check_num_token_accounts`) but chooses a mixed combination, e.g. `equality_proof_instruction_offset == 0` and `range_proof_instruction_offset != 0` (which really needs sysvar + eq-context + owner = 3 fixed accounts + signer(s) = 5 total minimum), the code:
1. Inserts `instructionsSysvar` at `account_indexes[2]` (`offset` becomes 3).
2. Tries to insert `equalityProofContextStateAccount` at `offset == 3`, but the guard `3 < account_indexes.len().saturating_sub(1) == 3` is `false`, so this insertion is silently skipped.
3. Calls `parse_signers(map, 3, ...)`, which attributes `account_keys[account_indexes[3]]` to the `"owner"` field.

The result: the account at index 3 — which is semantically the equality-proof context-state account slot per the real instruction layout — is mislabeled as `"owner"`, and the `equalityProofContextStateAccount` field is silently omitted from the parsed JSON, even though the sysvar-based has_sysvar/eq/rng logic implies it should exist. No panic occurs, but the RPC-visible parsed instruction data misattributes account roles.

### Impact Explanation
This is decoder misreporting reachable through `transaction-status` parsing, which is invoked by RPC methods such as `getTransaction`/`getConfirmedTransaction` for any transaction that references the `TokenInstruction::ConfidentialTransferExtension`/`Withdraw` variant — regardless of whether the instruction succeeds on-chain. An attacker only needs to submit (or have previously submitted, even if it fails execution) a single transaction with a crafted account list; any client later querying that transaction via RPC receives JSON output where a non-signer context-state account key is mislabeled as `"owner"`, and a legitimate proof-context field is missing. This matches the "decoder panic and misreporting" acceptable-impact category, though the severity is limited to display/labeling correctness (no consensus or fund-safety impact) since this code path is purely part of human-readable RPC transaction decoding.

### Likelihood Explanation
Fully attacker-controlled and reproducible with a single crafted transaction (one JSON-RPC submission, well within the rate constraints) — no validator/leader/staked-node privileges are required, and the instruction need not even succeed on-chain, since the parser runs independently of runtime execution results when producing "parsed" transaction JSON.

### Recommendation
Replace the fixed length-based guard (`offset < account_indexes.len().saturating_sub(1)`) with an explicit total-required-accounts calculation derived from `has_sysvar`, `equality_proof_instruction_offset == 0`, and `range_proof_instruction_offset == 0` (i.e., compute the exact number of leading fixed accounts needed and call `check_num_token_accounts` with that computed minimum before attempting any offset-based indexing), instead of relying on a generic `saturating_sub(1)` cutoff that silently drops fields when the account list is shorter than the specific combination actually requires.

### Proof of Concept
```rust
#[test]
fn test_withdraw_min_accounts_mixed_offsets_mislabels_owner() {
    let token_account = Pubkey::new_unique();
    let mint = Pubkey::new_unique();
    let authority = Pubkey::new_unique(); // will be placed at index 4, but tx only has 4 accounts

    // eq_offset == 0 (needs context account), rng_offset != 0 (needs sysvar)
    let context_eq = ProofLocation::ContextStateAccount(&Pubkey::new_unique());
    let offset_rng = ProofLocation::InstructionOffset(
        NonZero::new(1).unwrap(),
        &BatchedRangeProofU64Data::zeroed(),
    );

    let mut instruction = inner_withdraw(
        &spl_token_2022_interface::id(),
        &token_account,
        &mint,
        42,
        9,
        &PodAeCiphertext::default(),
        &authority,
        &[],
        context_eq,
        offset_rng,
    )
    .unwrap();

    // Force account_indexes.len() == 4: token, mint, sysvar, "owner" slot
    // (drop the real equality-context account that the actual program would require)
    instruction.accounts.truncate(4);

    let message = Message::new(&[instruction], None);
    let parsed = parse_token(
        &message.instructions[0],
        &AccountKeys::new(&message.account_keys, None),
    )
    .unwrap(); // must not panic

    // BUG: equalityProofContextStateAccount silently missing
    assert!(parsed.info.get("equalityProofContextStateAccount").is_none());
    // BUG: "owner" is actually message.account_keys[3], not a real signer/authority
    assert_eq!(
        parsed.info["owner"],
        json!(message.account_keys[3].to_string())
    );
}
```
Expected assertions: no panic (confirms the length guard prevents OOB), but `equalityProofContextStateAccount` is missing from `parsed.info` and `"owner"` is populated with the wrong account key, demonstrating the misattribution.

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
