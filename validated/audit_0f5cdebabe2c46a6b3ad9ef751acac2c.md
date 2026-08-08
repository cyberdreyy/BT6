### Title
Malformed `Withdraw` confidential-transfer instruction with truncated `account_indexes` causes `equalityProofContextStateAccount`/`rangeProofContextStateAccount` to be silently dropped and an unrelated account to be mislabeled as `owner` - ([File: transaction-status/src/parse_token/extension/confidential_transfer.rs])

### Summary
The `ConfidentialTransferInstruction::Withdraw` parser only requires `account_indexes.len() >= 4` via `check_num_token_accounts`, but the actual number of accounts needed depends on `equality_proof_instruction_offset`/`range_proof_instruction_offset` (up to 5+ when both proofs use context-state accounts). Because the loop bounds each optional field insertion with `offset < account_indexes.len().saturating_sub(1)` instead of validating against the true expected account count, a short `account_indexes` array causes the `rangeProofContextStateAccount` (or `instructionsSysvar`/`equalityProofContextStateAccount`) field to be silently omitted, and `parse_signers` is then invoked with an `offset` that points at what should have been that context-state account slot, mislabeling it as `owner`.

### Finding Description
`parse_confidential_transfer_instruction` for `Withdraw` decodes `WithdrawInstructionData` and only enforces a minimum account count of 4 via `check_num_token_accounts(account_indexes, 4)?` [1](#0-0) . It then walks through optional slots (`instructionsSysvar`, `equalityProofContextStateAccount`, `rangeProofContextStateAccount`) using `offset` starting at 2, gating each insertion with `offset < account_indexes.len().saturating_sub(1)`: [2](#0-1) 

Finally `parse_signers(map, offset, ...)` is called with whatever `offset` value the loop landed on [3](#0-2) .

If an attacker crafts a `Withdraw` instruction with `equality_proof_instruction_offset == 0` and `range_proof_instruction_offset == 0` (i.e., both proofs are supplied via separate context-state accounts, which on a real/valid instruction requires 5 accounts: `account, mint, equalityCtx, rangeCtx, owner`), but supplies only the minimum 4 accounts that `check_num_token_accounts` requires:
- `has_sysvar` is `false`, so the sysvar branch is skipped.
- The equality-context branch executes (`offset=2 < len()-1=3` is true), inserting `account_indexes[2]` as `equalityProofContextStateAccount` and bumping `offset` to 3.
- The range-context branch's guard `offset(3) < len()-1(3)` is `false`, so `rangeProofContextStateAccount` is silently omitted, and `offset` remains 3.
- `parse_signers` is then called with `offset = 3`, indexing `account_indexes[3]` — the account that was actually intended as the *range-proof context-state account* — and labels it `"owner"` in the parsed JSON.

This is reachable because `transaction-status` parses instruction data for any submitted `CompiledInstruction` (whether the transaction later succeeds or fails on-chain), so a single crafted transaction with a truncated account list is sufficient; a subsequent `getTransaction` (jsonParsed) call returns the mislabeled `owner` field, violating the invariant that the parsed authority/owner must correspond to the actual owner/authority account in the raw instruction.

### Impact Explanation
This is a decoder misreporting bug: the RPC `jsonParsed` transaction/instruction view can report an unrelated pubkey (the range-proof context-state account) as the `"owner"`/authority of a confidential-transfer withdrawal, while silently dropping the `rangeProofContextStateAccount` field. Any client, explorer, or indexer relying on the parsed JSON for authority/ownership display would show an incorrect signer/owner for the instruction. This falls under the accepted "decoder panic and misreporting" impact category, scoped strictly to the `transaction-status` parsing layer (no consensus or execution-state impact).

### Likelihood Explanation
Fully attacker-controlled and requires only a single, unprivileged transaction submission: the attacker crafts an SPL Token-2022 confidential-transfer `Withdraw` instruction with `equality_proof_instruction_offset = 0`, `range_proof_instruction_offset = 0`, and exactly 4 accounts (rather than the 5 actually required for that offset combination). The transaction may fail on-chain (e.g., due to missing accounts), but it still lands in a block and is retrievable via a single `getTransaction` call with `jsonParsed` encoding, satisfying the stated call-rate constraints. This is deterministic and trivially repeatable.

### Recommendation
Compute the exact number of accounts required based on `equality_proof_instruction_offset`/`range_proof_instruction_offset` (and similarly for `Transfer`/`TransferWithFee`) before entering the optional-field loop, and call `check_num_token_accounts` with that precise count instead of the flat minimum of 4. Alternatively, change each optional-field guard to check against the exact expected total count (accounting for how many optional slots remain) rather than the generic `len().saturating_sub(1)`, ensuring fields are only skipped when genuinely absent and `parse_signers` never receives an `offset` that lands on a slot which was supposed to hold a context-state account.

### Proof of Concept
```rust
// transaction-status/src/parse_token/extension/confidential_transfer.rs (test)
#[test]
fn test_withdraw_truncated_accounts_mislabels_owner() {
    use spl_token_confidential_transfer_proof_extraction::instruction::ProofLocation;

    let token_account = Pubkey::new_unique();
    let mint = Pubkey::new_unique();
    let equality_ctx = Pubkey::new_unique();
    let range_ctx = Pubkey::new_unique(); // should be rangeProofContextStateAccount, NOT owner

    // Both proofs via context-state accounts => real instruction needs 5 accounts:
    // [account, mint, equalityCtx, rangeCtx, owner]
    let instruction = inner_withdraw(
        &spl_token_2022_interface::id(),
        &token_account,
        &mint,
        42,
        9,
        &PodAeCiphertext::default(),
        &range_ctx, // pretend "authority" slot is actually range_ctx to simulate truncation
        &[],
        ProofLocation::ContextStateAccount(&equality_ctx),
        ProofLocation::ContextStateAccount(&range_ctx),
    )
    .unwrap();

    // Manually truncate to 4 accounts (drop the real owner account), simulating
    // an attacker-crafted instruction with insufficient accounts.
    let mut truncated = instruction.clone();
    truncated.accounts.truncate(4);

    let message = Message::new(&[truncated], None);
    let parsed = parse_token(
        &message.instructions[0],
        &AccountKeys::new(&message.account_keys, None),
    )
    .unwrap();

    // BUG: rangeProofContextStateAccount is missing...
    assert!(parsed.info.get("rangeProofContextStateAccount").is_none());
    // ...and "owner" is populated with range_ctx's pubkey instead of a real owner.
    assert_eq!(parsed.info["owner"], json!(range_ctx.to_string()));
}
```
Expected (fixed) behavior: the parser should either report `rangeProofContextStateAccount` correctly and require 5 accounts (rejecting a 4-account instruction via `check_num_token_accounts`), or otherwise never populate `"owner"` with a pubkey that is not the true trailing signer/owner slot.

### Citations

**File:** transaction-status/src/parse_token/extension/confidential_transfer.rs (L177-182)
```rust
        ConfidentialTransferInstruction::Withdraw => {
            check_num_token_accounts(account_indexes, 4)?;
            let withdrawal_data: WithdrawInstructionData =
                *decode_instruction_data(instruction_data).map_err(|_| {
                    ParseInstructionError::InstructionNotParsable(ParsableProgram::SplToken)
                })?;
```

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

**File:** transaction-status/src/parse_token/extension/confidential_transfer.rs (L228-235)
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
