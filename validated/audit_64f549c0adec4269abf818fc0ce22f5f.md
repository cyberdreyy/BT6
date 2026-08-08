### Title
Confidential Withdraw/Transfer decoder mislabels a proof context-state account as `owner` when the account list is truncated to the enforced minimum - ([File: transaction-status/src/parse_token/extension/confidential_transfer.rs])

### Summary
`parse_confidential_transfer_instruction`'s `Withdraw` (and equally `Transfer`/`TransferWithFee`) handler computes a running `offset` into `account_indexes` based on `equality_proof_instruction_offset`/`range_proof_instruction_offset`, but the static minimum enforced by `check_num_token_accounts(account_indexes, 4)` does not match the true minimum number of accounts required for every offset combination (up to 5 when both proofs use `ContextStateAccount`s instead of the instructions sysvar). When a transaction supplies exactly the enforced minimum (4) while both `equality_proof_instruction_offset == 0` and `range_proof_instruction_offset == 0`, the decoder silently drops the `rangeProofContextStateAccount` field and instead binds that account's `account_keys` entry to `owner` via `parse_signers`, even though it is not actually functioning as the token account owner/authority.

### Finding Description
In `transaction-status/src/parse_token/extension/confidential_transfer.rs`, the `Withdraw` branch does: [1](#0-0) 

`offset` starts at 2 and is incremented up to twice, each increment gated by `offset < account_indexes.len().saturating_sub(1)`. This guard correctly prevents any out-of-bounds index into `account_indexes` (offset never exceeds `len - 1`), so there is no panic risk in this arithmetic. However, the *minimum* accepted length is fixed at 4 regardless of which proof-offset combination is used: [2](#0-1) 

- If both proof offsets are non-zero (uses the instructions sysvar, no context accounts), the true minimum is `account, mint, sysvar, owner` = 4 accounts — matching the check.
- If both proof offsets are zero (both proofs use separate `ContextStateAccount`s), the true minimum is `account, mint, eqCtx, rangeCtx, owner` = 5 accounts — but the check still only requires 4.

When an instruction with `equality_proof_instruction_offset == 0`, `range_proof_instruction_offset == 0`, and exactly 4 `account_indexes` is decoded: `has_sysvar` is false so the first branch is skipped; the second branch inserts `equalityProofContextStateAccount` from index 2 and advances `offset` to 3; the third branch's guard `offset(3) < len(4).saturating_sub(1) == 3` is false, so `rangeProofContextStateAccount` is never inserted and `offset` remains 3. `parse_signers` is then called with `last_nonsigner_index = 3`: [3](#0-2) 

Because `accounts.len()(4) > last_nonsigner_index+1(4)` is false, the `else` branch fires and labels `account_keys[account_indexes[3]]` as `"owner"`. In a well-formed instruction that layout position (index 3) would be the `rangeProofContextStateAccount`, not the authority — the real `owner` slot (index 4) is simply absent from this truncated instruction. The existing unit tests (`test_withdraw`, `test_transfer`) only exercise instructions built via the SDK's `inner_withdraw`/`inner_transfer` helpers, which always emit the full, correctly-sized account list for each offset combination, so this truncated/malformed case is not covered: [4](#0-3) 

An attacker does not need any privilege beyond submitting an ordinary transaction: they can hand-craft a `CompiledInstruction` targeting the SPL Token-2022 program's `ConfidentialTransferInstruction::Withdraw` discriminator with `equality_proof_instruction_offset = 0`, `range_proof_instruction_offset = 0`, and only 4 accounts. This instruction will fail on-chain execution (the real program logic needs 5 accounts here), but a failed transaction is still committed to the ledger and is fully retrievable via `getTransaction`; RPC's `jsonParsed` decoding runs over the instruction regardless of execution outcome, producing the mislabeled output. `DepositInstructionData.decimals` and `WithdrawInstructionData.decimals` themselves are simply attacker-supplied instruction-data fields that are faithfully echoed by the decoder — that part is expected decoding behavior, not a vulnerability, since these fields are not authoritative until validated by the real program logic.

### Impact Explanation
This is a decoder misreporting bug confined to the RPC `jsonParsed` transaction-status decoding: `getTransaction`/`getConfirmedTransaction` will report an incorrect `owner` field (actually the range-proof context-state account, or in other truncations a completely unrelated slot) for a crafted, minimally-sized `ConfidentialTransfer::Withdraw`/`Transfer`/`TransferWithFee` instruction. No panic occurs (bounds arithmetic is safe via `saturating_sub`), no consensus state is affected, and no funds move incorrectly on-chain — the mislabeling is purely a client-facing JSON misreport. This matches the "decoder panic and misreporting" acceptable impact category from the audit scope.

### Likelihood Explanation
Trivially reproducible by any single unprivileged client: craft one instruction with the described byte layout and account list, submit it once (it may even fail execution and still be queryable), then call `getTransaction` with `jsonParsed` encoding. No special preconditions beyond constructing raw instruction bytes/accounts are required.

### Recommendation
Make `check_num_token_accounts` (or add an additional check) dynamic per proof-offset combination in the `Withdraw`, `Transfer`, `TransferWithFee`, and `ConfidentialMintBurn` `Mint`/`Burn` handlers: compute the exact number of accounts required for the specific `has_sysvar`/`eq==0`/`range==0` combination before parsing, and reject (return `ParseInstructionError::InstructionNotParsable`) if `account_indexes.len()` is smaller than that exact requirement, instead of relying on a single global minimum. Additionally, `parse_signers` should not silently treat a leftover/ambiguous slot as `owner` when the preceding conditional insertions did not consume the expected number of proof-context accounts.

### Proof of Concept
```rust
// transaction-status/src/parse_token/extension/confidential_transfer.rs (test module)
#[test]
fn test_withdraw_truncated_accounts_mislabels_owner() {
    use solana_instruction::{AccountMeta, Instruction};
    use spl_token_2022_interface::extension::confidential_transfer::instruction::{
        ConfidentialTransferInstruction, WithdrawInstructionData,
    };

    let token_account = Pubkey::new_unique();
    let mint = Pubkey::new_unique();
    let equality_ctx_or_bogus = Pubkey::new_unique(); // will be mislabeled as owner
    let program_id = spl_token_2022_interface::id();

    // Hand-craft WithdrawInstructionData with both proof offsets == 0
    // (i.e. both proofs expected to be ContextStateAccounts), matching the
    // check_num_token_accounts(4) minimum instead of the true minimum of 5.
    let withdraw_data = WithdrawInstructionData {
        amount: 42u64.into(),
        decimals: 9,
        new_decryptable_available_balance: Default::default(),
        equality_proof_instruction_offset: 0,
        range_proof_instruction_offset: 0,
    };
    let mut data = vec![/* ConfidentialTransferInstruction discriminator bytes */];
    data.extend_from_slice(bytemuck::bytes_of(&withdraw_data));

    let instruction = Instruction {
        program_id,
        accounts: vec![
            AccountMeta::new(token_account, false),        // index 0: account
            AccountMeta::new_readonly(mint, false),         // index 1: mint
            AccountMeta::new_readonly(equality_ctx_or_bogus, false), // index 2: eqCtx (only proof account present)
            AccountMeta::new_readonly(Pubkey::new_unique(), true),   // index 3: should be rangeCtx, but gets mislabeled owner
        ],
        data,
    };

    let message = Message::new(&[instruction], None);
    let parsed = parse_token(
        &message.instructions[0],
        &AccountKeys::new(&message.account_keys, None),
    )
    .unwrap(); // no panic

    // BUG: the account at index 3 (intended as rangeProofContextStateAccount)
    // is reported as "owner", and rangeProofContextStateAccount is missing,
    // even though no real owner/authority account was ever supplied.
    assert!(parsed.info.get("rangeProofContextStateAccount").is_none());
    assert_ne!(parsed.info["owner"], json!(message.account_keys[3].to_string())
        /* expected to fail: shows decoder mislabels index 3 as owner */);
}
```
Expected assertions for a broader fuzz/invariant harness: for all `account_indexes.len()` in `[check_num_token_accounts minimum .. 8]` and all `(eq_offset, range_offset)` combinations, (a) `parse_token` never panics, and (b) whenever the number of accounts is insufficient for the given offset combination's true required layout, the decoder must either reject the instruction (`InstructionNotParsable`) or omit `owner` rather than binding it to a non-authority account slot.

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

**File:** transaction-status/src/parse_token/extension/confidential_transfer.rs (L877-896)
```rust
            ("All Contexts", context_eq, context_rng, 0, 0),
            ("All Offsets", offset_eq, offset_rng_2, 1, 2),
            ("Mixed C-I", context_eq, offset_rng_1, 0, 1),
            ("Mixed I-C", offset_eq, context_rng, 1, 0),
        ];

        for (name, eq_proof, rng_proof, eq_offset, rng_offset) in cases {
            let instruction = inner_withdraw(
                &spl_token_2022_interface::id(),
                &token_account,
                &mint,
                42, // amount
                9,  // decimals
                &PodAeCiphertext::default(),
                &authority,
                &[],
                eq_proof,
                rng_proof,
            )
            .unwrap();
```

**File:** transaction-status/src/parse_token.rs (L937-961)
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
}
```
