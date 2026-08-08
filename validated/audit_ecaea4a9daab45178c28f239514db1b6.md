### Title
Withdraw confidential-transfer decoder misreports owner/context-state accounts due to inconsistent minimum-account count vs. proof-offset combination - ([File: transaction-status/src/parse_token/extension/confidential_transfer.rs])

### Summary
The `Withdraw` arm of `parse_confidential_transfer_instruction` enforces only a static minimum of 4 accounts via `check_num_token_accounts(account_indexes, 4)`, but the actual number of accounts required depends on the combination of `equality_proof_instruction_offset`/`range_proof_instruction_offset` values. When both offsets are `0` (both proofs given via context-state accounts), 5 accounts are logically required (account, mint, equality-proof-ctx, range-proof-ctx, owner), yet the decoder's account-walking loop tolerates exactly 4, causing the last "reserved-for-proof" account to be silently reclassified as the owner/signer input to `parse_signers`.

### Finding Description
`ConfidentialTransferInstruction::Withdraw` (transaction-status/src/parse_token/extension/confidential_transfer.rs:177-240) starts `offset = 2` and conditionally advances it while inserting `instructionsSysvar`, `equalityProofContextStateAccount`, and `rangeProofContextStateAccount` fields, guarded each time by `offset < account_indexes.len().saturating_sub(1)`: [1](#0-0) 

This guard is meant to always leave at least one account for `parse_signers`, but it does not account for the number of context-state accounts actually implied by the proof-offset flags. With `account_indexes.len() == 4` (the enforced minimum) and both `equality_proof_instruction_offset == 0` and `range_proof_instruction_offset == 0` (i.e., `has_sysvar == false`, both proofs supplied as context-state accounts), the trace is:
- `offset = 2`; sysvar block skipped (`has_sysvar` false).
- equality block: `offset(2) < len-1(3)` → true → inserts `equalityProofContextStateAccount = account_indexes[2]`, `offset = 3`.
- range block: `offset(3) < len-1(3)` → false → **skipped**, even though `range_proof_instruction_offset == 0` indicates a range-proof context-state account is expected at `account_indexes[3]`.
- `parse_signers(map, offset=3, ...)` then treats `account_indexes[3..]` — i.e. the actual range-proof context-state account — as the owner/multisig-signer set.

The real spl-token-2022 program, for this offset combination, requires 5 accounts (account, mint, equality-ctx, range-ctx, authority/owner), so a transaction submitted with only 4 accounts would fail on-chain execution but is still included in the block (fee paid, instruction recorded) since Solana retains failed transactions. `parse_token`'s decoding path is not gated on transaction execution success, so `getTransaction` with `jsonParsed` encoding will still run this parsing logic and emit a misleading `owner` field pointing at what is actually the range-proof context-state account, while omitting `rangeProofContextStateAccount` entirely.

### Impact Explanation
This is a decoder misreporting bug reachable via a single, unprivileged RPC call sequence: submit one crafted (on-chain-failing) `Withdraw` ConfidentialTransferExtension instruction with exactly 4 accounts and both proof offsets `= 0`, then call `getTransaction` with `jsonParsed` encoding. The parsed JSON incorrectly attributes the `owner`/authority field to a context-state account rather than the real signer, and silently drops the `rangeProofContextStateAccount` field — corrupting the authority/account relationship reported to any client relying on `jsonParsed` output. This matches the in-scope "decoder panic and misreporting" bounty category; it does not affect consensus or on-chain state.

### Likelihood Explanation
Fully attacker-controlled and reproducible with a single unprivileged transaction submission (no special account count beyond compiling a 4-account instruction) plus one `getTransaction` RPC call — well within the one-call-per-`CLUSTER_SLOT_TIME_TARGET/2` constraint. The transaction need not succeed on-chain for its instruction data/accounts to be recorded and later decoded, since failed transactions remain in the block. No special privileges, keys, or validator control are required.

### Recommendation
Make the minimum-account check for `Withdraw` (and the analogous `Transfer`/`TransferWithFee` arms, which share the same pattern) proof-offset-aware: compute the exact required account count based on `has_sysvar` plus the number of `== 0` proof offsets (each requiring its own context-state account) before calling `check_num_token_accounts`, or change the `offset < account_indexes.len().saturating_sub(1)` guard to `offset < account_indexes.len()` combined with a final assertion that exactly one signer-eligible account remains, returning `ParseInstructionError::InstructionKeyMismatch` if the account count doesn't match what the given offset combination requires.

### Proof of Concept
```rust
// transaction-status/src/parse_token/extension/confidential_transfer.rs (test module)
#[test]
fn test_withdraw_min_accounts_misreport() {
    let token_account = Pubkey::new_unique();
    let mint = Pubkey::new_unique();
    let equality_ctx = Pubkey::new_unique();
    let range_ctx = Pubkey::new_unique(); // will be misreported as "owner"

    // Both proofs as context-state accounts (offsets == 0) -> real program
    // needs 5 accounts: account, mint, eq_ctx, range_ctx, authority.
    let instruction = inner_withdraw(
        &spl_token_2022_interface::id(),
        &token_account,
        &mint,
        42, 9,
        &PodAeCiphertext::default(),
        &range_ctx, // stand-in "authority" slot actually holds the range ctx pubkey
        &[],
        ProofLocation::ContextStateAccount(&equality_ctx),
        ProofLocation::ContextStateAccount(&range_ctx),
    ).unwrap();

    // Truncate to exactly 4 accounts to simulate the on-chain-failing,
    // but still decodable, crafted transaction.
    let mut instruction = instruction;
    instruction.accounts.truncate(4);

    let message = Message::new(&[instruction], None);
    let parsed = parse_token(
        &message.instructions[0],
        &AccountKeys::new(&message.account_keys, None),
    ).unwrap();

    // Bug: "owner" ends up equal to the range-proof context-state account,
    // and "rangeProofContextStateAccount" is missing entirely.
    assert_eq!(parsed.info["owner"], json!(range_ctx.to_string()));
    assert!(parsed.info.get("rangeProofContextStateAccount").is_none());
}
```
Expected (correct) behavior: the decoder should either reject this account layout (mismatched account count for the given proof-offset combination) or correctly report `rangeProofContextStateAccount` separately from `owner`; the assertions above demonstrate the current misattribution.

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
