### Title
Transfer proof-account mislabeling due to conservative `saturating_sub(1)` boundary skips proof-context fields and reassigns them as "owner" - ([File: transaction-status/src/parse_token/extension/confidential_transfer.rs])

### Summary
In the `ConfidentialTransferInstruction::Transfer` branch of `parse_confidential_transfer_instruction`, the optional-account offset walk is gated by `offset < account_indexes.len().saturating_sub(1)` for every proof-context slot instead of `offset < account_indexes.len()`. When `account_indexes.len()` sits at the branch's minimum boundary (4) while the instruction payload still signals `ContextStateAccount` proof locations (offset fields == 0), the reserved-slot check fails before any proof account is consumed, and the account actually holding a proof-context pubkey is instead handed to `parse_signers` and labeled `"owner"`. No panic occurs (indexing stays in-bounds), but the parsed JSON misrepresents a real on-chain account.

### Finding Description
`check_num_token_accounts(account_indexes, 4)` at line 242 only guarantees `account_indexes.len() >= 4`; it does not correlate the length with how many optional accounts the encoded proof-location flags (`equality_proof_instruction_offset`, `ciphertext_validity_proof_instruction_offset`, `range_proof_instruction_offset`) actually require [1](#0-0) .

The subsequent walk uses `offset < account_indexes.len().saturating_sub(1)` for each of the four optional slots (sysvar, equality, ciphertext-validity, range) [2](#0-1) . With `account_indexes.len() == 4`, `saturating_sub(1) == 3`, and `offset` starts at `3`, so `3 < 3` is `false` for every check — none of the optional fields are populated, regardless of the proof-location flags in the instruction data.

`offset` remains `3` and is passed unchanged into `parse_signers(map, offset, ...)` [3](#0-2) , which reads `account_keys[account_indexes[3]]` and labels it `"owner"`.

If the attacker crafts (and gets included on-chain, e.g. as a failed transaction — Solana blocks include failed transactions and RPC `getTransaction` still parses their instructions) a Transfer instruction with exactly 4 accounts where `account_indexes[3]` is actually meant as `equalityProofContextStateAccount` (flags signal offset == 0, i.e. `ContextStateAccount`), the parser:
- Does not emit `equalityProofContextStateAccount` (or `ciphertextValidityProofContextStateAccount`/`rangeProofContextStateAccount`) even though that account genuinely exists in the instruction's account list.
- Mislabels that same account as `"owner"` in the parsed JSON, which is normally expected to be a signing authority.

This is a real misrepresentation: an integrator relying on `getTransaction` jsonParsed output would see a bogus `"owner"` pubkey (actually a proof-context state account, not a signer) and would be missing the legitimate proof-context account field(s) entirely. No indexing exceeds `account_indexes.len()` bounds (the `saturating_sub(1)` guard, combined with the branch's `check_num_token_accounts` floor of 4, prevents underflow and out-of-bounds slices), so there is no panic.

### Impact Explanation
Scoped impact is "decoder panic and misreporting" — specifically misreporting. `getTransaction` with `jsonParsed` encoding will return a parsed `confidentialTransfer` instruction whose `"owner"` field is actually an unrelated proof-context state account, and will silently drop the true `equalityProofContextStateAccount`/`ciphertextValidityProofContextStateAccount`/`rangeProofContextStateAccount` fields that correspond to accounts genuinely referenced by the instruction. This misleads any integrator, wallet, or auditing tool that inspects confidential-transfer proof accounts or authority via the parsed JSON. No panic and no consensus/state impact occurs; the flaw is confined to the transaction-status parsing/formatting layer.

### Likelihood Explanation
The precondition (`account_indexes.len()` at the handler's minimum of 4, combined with proof-location flags claiming `ContextStateAccount` for one or more of the three proof types) is fully attacker-controlled via ordinary instruction construction, and does not require the transaction to succeed on-chain — a transaction that fails on-chain (e.g., because the SPL Token-2022 program itself would reject the mismatched account layout) is still included in the block and returned by `getTransaction`, since the transaction-status parser processes both successful and failed transactions' instructions unconditionally. This makes the misreporting trivially and repeatably reachable with a single RPC call to fetch a previously-submitted transaction.

### Recommendation
Replace the `saturating_sub(1)` boundary in all optional-account walks (Withdraw, Transfer, TransferWithFee branches) with a check that is aware of how many optional accounts are actually implied by the proof-location flags, e.g. `offset < account_indexes.len()` combined with an explicit reservation of exactly one trailing slot for the authority/signer group, or better, precompute the exact expected `account_indexes.len()` from the flag combination and validate it up front (returning `ParseInstructionError::InstructionNotParsable` on mismatch) before attempting to label any accounts.

### Proof of Concept
```rust
// transaction-status/src/parse_token/extension/confidential_transfer.rs (test module)
#[test]
fn test_transfer_boundary_misreport() {
    let source = Pubkey::new_unique();
    let mint = Pubkey::new_unique();
    let destination = Pubkey::new_unique();
    let equality_ctx = Pubkey::new_unique(); // will end up mislabeled

    // Build a raw Transfer instruction with exactly 4 accounts
    // (source, mint, destination, equality_ctx) and proof-offset
    // fields all == 0 (ContextStateAccount), i.e. as if 3 proof-context
    // accounts + signer were expected but only 1 extra account supplied.
    let transfer_data = TransferInstructionData {
        new_source_decryptable_available_balance: PodAeCiphertext::default(),
        equality_proof_instruction_offset: 0,
        ciphertext_validity_proof_instruction_offset: 0,
        range_proof_instruction_offset: 0,
        ..Default::default() // adjust per actual struct fields
    };

    let instruction = /* construct raw Instruction with accounts
        [source, mint, destination, equality_ctx] and above data,
        program spl_token_2022_interface::id() */;

    let message = Message::new(&[instruction], None);
    let parsed = parse_token(
        &message.instructions[0],
        &AccountKeys::new(&message.account_keys, None),
    )
    .unwrap();

    // BUG: equalityProofContextStateAccount is missing even though
    // equality_ctx is a real account referenced by the instruction.
    assert!(parsed.info.get("equalityProofContextStateAccount").is_none());

    // BUG: the same account is mislabeled as "owner".
    assert_eq!(parsed.info["owner"], json!(equality_ctx.to_string()));
}
```
Expected result confirms no panic occurs but the account at `account_indexes[3]` (a genuine proof-context account) is both omitted from its correct field and relabeled `"owner"`, demonstrating the misreporting described above.

### Citations

**File:** transaction-status/src/parse_token/extension/confidential_transfer.rs (L241-262)
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

```

**File:** transaction-status/src/parse_token/extension/confidential_transfer.rs (L263-299)
```rust
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

**File:** transaction-status/src/parse_token/extension/confidential_transfer.rs (L301-308)
```rust
            parse_signers(
                map,
                offset,
                account_keys,
                account_indexes,
                "owner",
                "multisigOwner",
            );
```
