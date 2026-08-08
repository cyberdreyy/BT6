### Title
Boundary condition in `Withdraw` offset-walking logic causes an account meant to be a proof-context-state account to be mislabeled as the transaction "owner"/signer in `jsonParsed` output - ([File: transaction-status/src/parse_token/extension/confidential_transfer.rs])

### Summary
The `ConfidentialTransferInstruction::Withdraw` arm walks a running `offset` counter through `account_indexes`, gating each field insertion with `offset < account_indexes.len().saturating_sub(1)`. [1](#0-0)  An attacker who crafts a `Withdraw` instruction with the minimum account count accepted by `check_num_token_accounts(account_indexes, 4)` combined with non-zero/zero offset flags can make the boundary check reject the `equalityProofContextStateAccount`/`rangeProofContextStateAccount` insertion one index earlier than it should, so that index is instead consumed by `parse_signers` and rendered as `"owner"` in the parsed JSON. [2](#0-1) 

### Finding Description
`parse_confidential_transfer_instruction`'s `Withdraw` branch only enforces a minimum of 4 accounts via `check_num_token_accounts(account_indexes, 4)`, then decodes `equality_proof_instruction_offset`/`range_proof_instruction_offset` straight from attacker-supplied instruction data. [3](#0-2)  It computes `has_sysvar` from those two offsets, and then walks `offset` from `2`, gating each conditional insertion (`instructionsSysvar`, then `equalityProofContextStateAccount`, then `rangeProofContextStateAccount`) with `offset < account_indexes.len().saturating_sub(1)`. [1](#0-0) 

Because `account_indexes.len()` is attacker-controlled (subject only to the `>= 4` floor) and independent from the offset flags that determine how many "extra" accounts are semantically expected, an attacker can choose a combination where an intermediate `saturating_sub(1)` check evaluates `false` one step too early. For example, with exactly 4 accounts and `equality_proof_instruction_offset != 0` (so `has_sysvar` is true and consumes index 2 as `instructionsSysvar`), the subsequent `range_proof_instruction_offset == 0 && offset < len - 1` check becomes `3 < 3`, which is false, so `rangeProofContextStateAccount` is never inserted even though `range_proof_instruction_offset == 0` says a context-state account is present. `offset` remains `3`, and that index is then passed unchanged into `parse_signers(map, offset, ...)`, which renders `account_keys[account_indexes[3]]` as `"owner"`. [4](#0-3)  The actual role of that account (a proof context-state account, not a signer) is silently dropped/relabeled.

No out-of-bounds panic occurs because `check_num_token_accounts` guarantees `account_indexes.len() >= 4` and every array access is additionally gated by `offset < len - 1` before use, and `saturating_sub` prevents underflow. [5](#0-4)  The existing unit test `test_withdraw` only exercises the four "matched" offset/account-count combinations that the SDK's own instruction builder would produce and does not fuzz mismatched account-list lengths, so this boundary gap is untested. [6](#0-5) 

### Impact Explanation
This is a decoder-misreporting issue in the `jsonParsed` transaction/instruction decoding path used by `getTransaction`. A single crafted (never necessarily executed on-chain, since parsing happens independent of program execution success/failure) `Withdraw` instruction can cause the RPC-facing "parsed" view to mislabel a proof-context-state account as the transaction's signing `"owner"`, or conversely omit/misplace the correct context-account field. This does not cause a crash, does not mutate consensus state, and does not permit unbounded cost — it is scoped to incorrect account-role labeling returned by a read-only RPC call, matching the "decoder panic and misreporting" bounty category.

### Likelihood Explanation
Fully attacker-controlled and requires only submitting one crafted transaction (or even just constructing raw instruction bytes/account lists off-chain and having any transaction reference them) followed by a single `getTransaction(jsonParsed)` call. No special privileges, staking, or leader control needed. The instruction does not need to succeed on-chain for the parser to run on it as long as it appears in a confirmed transaction message (parsing occurs from instruction bytes + account keys regardless of program execution outcome, since `parse_token` only depends on `CompiledInstruction` and `AccountKeys`). [7](#0-6) 

### Recommendation
Decouple the "does this proof need a context-state account" decision from the raw index/length walk: precompute the total number of expected trailing accounts (sysvar + context accounts) from the offset flags before consuming any of `account_indexes`, verify `account_indexes.len()` is large enough to hold all of them plus at least one signer, and reject with `ParseInstructionError::InstructionNotParsable` if not — instead of silently short-circuiting the loop partway through and letting `parse_signers` consume an index that was meant for a context-state account.

### Proof of Concept
```rust
// transaction-status/src/parse_token/extension/confidential_transfer.rs
#[test]
fn test_withdraw_boundary_offset_mislabels_context_account_as_owner() {
    let token_account = Pubkey::new_unique();
    let mint = Pubkey::new_unique();
    let range_ctx = Pubkey::new_unique(); // intended as rangeProofContextStateAccount

    // eq offset != 0 (uses instruction offset -> needs instructionsSysvar)
    // range offset == 0 (needs a context-state account) but only 4 accounts total.
    let offset_eq = ProofLocation::InstructionOffset(
        NonZero::new(1).unwrap(),
        &CiphertextCommitmentEqualityProofData::zeroed(),
    );
    let context_rng = ProofLocation::ContextStateAccount(&range_ctx);

    let instruction = inner_withdraw(
        &spl_token_2022_interface::id(),
        &token_account,
        &mint,
        42,
        9,
        &PodAeCiphertext::default(),
        &range_ctx, // authority slot deliberately filled with the range-ctx pubkey
        &[],
        offset_eq,
        context_rng,
    )
    .unwrap();

    // Manually truncate the account list to the minimum of 4 accounts,
    // simulating an attacker-crafted account_indexes shorter than the
    // sysvar+context-account combination actually requires.
    let message = Message::new(&[instruction], None);
    let compiled = &message.instructions[0];
    let truncated_accounts = &compiled.accounts[..4];

    let parsed = parse_confidential_transfer_instruction(
        &compiled.data,
        truncated_accounts,
        &AccountKeys::new(&message.account_keys, None),
    ).unwrap();

    // Expected per spec: rangeProofContextStateAccount == range_ctx.
    // Actual (bug): rangeProofContextStateAccount is missing, and
    // "owner" is populated from the account that should have been range_ctx.
    assert!(
        parsed.info.get("rangeProofContextStateAccount").is_none(),
        "bug reproduced: rangeProofContextStateAccount silently dropped"
    );
    assert_eq!(
        parsed.info["owner"],
        json!(range_ctx.to_string()),
        "bug reproduced: proof context account mislabeled as owner/signer"
    );
}
```
Fuzz-test extension: iterate all 4 boolean combinations of `(equality_proof_instruction_offset == 0, range_proof_instruction_offset == 0)` crossed with `account_indexes.len() in [4, 5, 6, 7]`, assert (a) no panic, and (b) for every offset that is `0`, the corresponding `*ProofContextStateAccount` field is present and equals the account at the position dictated by the real spl-token-2022 instruction builder's account ordering — never silently absorbed into `"owner"`/`"multisigOwner"`.

### Citations

**File:** transaction-status/src/parse_token/extension/confidential_transfer.rs (L177-235)
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

            parse_signers(
                map,
                offset,
                account_keys,
                account_indexes,
                "owner",
                "multisigOwner",
            );
```

**File:** transaction-status/src/parse_token/extension/confidential_transfer.rs (L846-988)
```rust
    #[test]
    fn test_withdraw() {
        let token_account = Pubkey::new_unique();
        let mint = Pubkey::new_unique();
        let authority = Pubkey::new_unique();

        let equality_ctx = Pubkey::new_unique();
        let range_ctx = Pubkey::new_unique();

        let offset_eq = ProofLocation::InstructionOffset(
            NonZero::new(1).unwrap(),
            &CiphertextCommitmentEqualityProofData::zeroed(),
        );
        let offset_rng_2 = ProofLocation::InstructionOffset(
            NonZero::new(2).unwrap(),
            &BatchedRangeProofU64Data::zeroed(),
        );
        let offset_rng_1 = ProofLocation::InstructionOffset(
            NonZero::new(1).unwrap(),
            &BatchedRangeProofU64Data::zeroed(),
        );

        let context_eq = ProofLocation::ContextStateAccount(&equality_ctx);
        let context_rng = ProofLocation::ContextStateAccount(&range_ctx);

        // We test exhaustive combinations to ensure the parser's offset logic holds.
        // Legend:
        // 'C' = Context State Account (Proof is in a separate account)
        // 'I' = Instruction Offset (Proof is bundled in the instruction data payload)
        // Array of cases: (Test Name, Eq Proof, Rng Proof, Eq Offset, Rng Offset)
        let cases = vec![
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

            check_no_panic(instruction.clone());

            let message = Message::new(&[instruction], None);
            let parsed = parse_token(
                &message.instructions[0],
                &AccountKeys::new(&message.account_keys, None),
            )
            .unwrap();

            assert_eq!(
                parsed.instruction_type, "withdrawConfidentialTransfer",
                "Failed on: {name}",
            );
            assert_eq!(
                parsed.info["account"],
                json!(token_account.to_string()),
                "Failed on: {name}",
            );
            assert_eq!(
                parsed.info["mint"],
                json!(mint.to_string()),
                "Failed on: {name}",
            );
            assert_eq!(
                parsed.info["owner"],
                json!(authority.to_string()),
                "Failed on: {name}",
            );
            assert_eq!(parsed.info["amount"], json!(42), "Failed on: {name}");
            assert_eq!(parsed.info["decimals"], json!(9), "Failed on: {name}");
            assert_eq!(
                parsed.info["newDecryptableAvailableBalance"],
                json!(format!("{}", PodAeCiphertext::default())),
                "Failed on: {name}",
            );

            assert_eq!(
                parsed.info["equalityProofInstructionOffset"],
                json!(eq_offset),
                "Failed on: {name}",
            );
            assert_eq!(
                parsed.info["rangeProofInstructionOffset"],
                json!(rng_offset),
                "Failed on: {name}",
            );

            // Sysvar Assertion: Only present if at least one proof relies on
            // an instruction offset
            if eq_offset != 0 || rng_offset != 0 {
                assert_eq!(
                    parsed.info["instructionsSysvar"],
                    json!(sysvar::instructions::id().to_string()),
                    "Sysvar mismatch on: {name}",
                );
            } else {
                assert!(
                    parsed.info.get("instructionsSysvar").is_none(),
                    "Sysvar mismatch on: {name}",
                );
            }

            if eq_offset == 0 {
                assert_eq!(
                    parsed.info["equalityProofContextStateAccount"],
                    json!(equality_ctx.to_string()),
                    "Eq Context mismatch on: {name}",
                );
            } else {
                assert!(
                    parsed
                        .info
                        .get("equalityProofContextStateAccount")
                        .is_none(),
                    "Eq Context mismatch on: {name}",
                );
            }

            if rng_offset == 0 {
                assert_eq!(
                    parsed.info["rangeProofContextStateAccount"],
                    json!(range_ctx.to_string()),
                    "Rng Context mismatch on: {name}",
                );
            } else {
                assert!(
                    parsed.info.get("rangeProofContextStateAccount").is_none(),
                    "Rng Context mismatch on: {name}",
                );
            }
        }
```

**File:** transaction-status/src/parse_token.rs (L30-43)
```rust
pub fn parse_token(
    instruction: &CompiledInstruction,
    account_keys: &AccountKeys,
) -> Result<ParsedInstructionEnum, ParseInstructionError> {
    match instruction.accounts.iter().max() {
        Some(index) if (*index as usize) < account_keys.len() => {}
        _ => {
            // Runtime should prevent this from ever happening
            return Err(ParseInstructionError::InstructionKeyMismatch(
                ParsableProgram::SplToken,
            ));
        }
    }
    if let Ok(token_instruction) = TokenInstruction::unpack(&instruction.data) {
```
