### Title
`parse_confidential_burn` account-offset heuristic can mislabel the burn authority/owner in JSON-parsed transactions - (File: `transaction-status/src/parse_token/extension/permissioned_burn.rs`)

### Summary
The `PermissionedBurnInstruction::ConfidentialBurn` parser in `transaction-status/src/parse_token/extension/permissioned_burn.rs` derives the boundary between optional proof-related accounts and the trailing "permissioned burn authority" / "owner-or-delegate" accounts purely from the *total number of accounts present in the raw instruction* (`account_indexes.len()`), rather than from a value that is cryptographically or structurally tied to the actual proof-offset semantics. Any unprivileged user who can submit a transaction (even one that ultimately fails execution) fully controls both the instruction data (the three proof-instruction offsets) and the account list length/order, and can therefore manipulate which account gets labeled `instructionsSysvar`, `*ProofContextStateAccount`, `permissionedBurnAuthority`, or `authority`/`multisigAuthority` in the `jsonParsed` output returned by RPC methods such as `getTransaction`/`getConfirmedTransaction`.

### Finding Description
`parse_confidential_burn_instruction` walks the accounts array using a mutable `offset` cursor, and at each step decides whether to consume another slot for `instructionsSysvar` or one of the three `*ProofContextStateAccount` fields based on two things: (1) whether the corresponding proof-instruction-offset field in `instruction_data` is zero/non-zero, and (2) whether `offset < account_indexes.len().saturating_sub(2)` (or `saturating_sub(1)` for the last check): [1](#0-0) 

The comment states the intent: "the permissioned burn authority and the owner/delegate are always the trailing 2+ accounts," so remaining "room" is computed as `len - 2`. But `account_indexes` is attacker-supplied raw instruction account data taken directly off the wire — there is no requirement, at the transaction-status parsing layer, that the number of accounts actually matches what the real spl-token-2022 program would enforce at execution time. The parser is invoked purely as a best-effort decoder for display (e.g., via `getTransaction` with `encoding=jsonParsed`), independent of whether the instruction succeeds on-chain: [2](#0-1) 

By choosing the padding/length of the account list together with the three `equality/ciphertext/range_proof_instruction_offset` fields in the instruction data, a user can cause the `offset < ...saturating_sub(N)` guards to evaluate differently than intended, shifting which physical account index ends up tagged as `instructionsSysvar`, a `*ProofContextStateAccount`, `permissionedBurnAuthority`, or the final `authority`/`multisigAuthority` (via `parse_signers`): [3](#0-2) 

This mirrors the root cause pattern in the external report: a downstream component derives a security-relevant identity/role (there: `VAULT.deposit(_assetAmount, address(this))` instead of validating `_depositor`; here: which account is "the authority") from a value that is not actually authoritative, allowing the true relationship between accounts and roles to be spoofed for any observer relying on that derived labeling.

### Impact Explanation
This does not affect consensus, bank state, or runtime execution — it only affects the human-readable `jsonParsed` instruction metadata returned by transaction-status RPC methods (`getTransaction`, `getConfirmedTransaction`, `getBlock` with parsed encoding, etc.). Block explorers, wallets, and monitoring tooling that trust this metadata to attribute a "permissioned burn authority" or "owner" to a specific address for a `ConfidentialBurn` instruction can be misled about which account actually held that role in the transaction, i.e., wrong/misreported account data is returned for a query — a decoder misreporting bug per the accepted impact classes.

### Likelihood Explanation
Any unprivileged user can craft and submit (or even just have processed and recorded, since parsing is independent of execution success) a transaction invoking the SPL Token-2022 program with a `TokenInstruction::ConfidentialMintBurnExtension` / `PermissionedBurnInstruction::ConfidentialBurn` discriminator, arbitrary account list length, and arbitrary proof-offset values in the instruction data. No special privilege, validator role, or multi-client coordination is required — a single crafted transaction plus a single subsequent `getTransaction` call is sufficient to reproduce the mislabeling.

### Recommendation
Do not derive the account-boundary/offset logic from `account_indexes.len()` alone. Instead, compute the number of optional proof accounts strictly and independently from the three proof-instruction-offset flags (each contributes exactly 0 or 1 slot, plus the shared `instructionsSysvar` slot if any offset is non-zero), and treat any mismatch between that computed expected account count and the actual `account_indexes.len()` as a parse failure (return `ParseInstructionError::InstructionNotParsable`) rather than silently reinterpreting different accounts as different fields.

### Proof of Concept
1. Construct a raw `CompiledInstruction` targeting the SPL Token-2022 program ID with the `ConfidentialMintBurnExtension` prefix byte followed by a `PermissionedBurnInstruction::ConfidentialBurn` payload where `equality_proof_instruction_offset`, `ciphertext_validity_proof_instruction_offset`, and `range_proof_instruction_offset` are chosen (e.g., all non-zero) so that `has_sysvar` is `true`.
2. Supply exactly 4 accounts (`account`, `mint`, `arbitrary_A`, `arbitrary_B`) — the minimum accepted by `check_num_token_accounts(account_indexes, 4)`.
3. Call `parse_token` (as exercised by `transaction-status`'s `jsonParsed` path) on this instruction: because `offset (2) < account_indexes.len().saturating_sub(2) (2)` is `false`, the `instructionsSysvar` field is skipped even though `has_sysvar` is `true`, and `arbitrary_A` (index 2) is instead labeled `permissionedBurnAuthority` while `arbitrary_B` (index 3) is labeled `authority`, even though the offsets in the data claim proofs are supplied via separate instructions (requiring an instructions sysvar account) rather than via `arbitrary_A`.
4. Compare against the equivalent instruction built with the real `spl_token_2022_interface` client-side builder (which would include the sysvar account) to observe the label/account mismatch reported by `getTransaction(..., {encoding: "jsonParsed"})`.

### Citations

**File:** transaction-status/src/parse_token/extension/permissioned_burn.rs (L90-103)
```rust
        PermissionedBurnInstruction::ConfidentialBurn => {
            check_num_token_accounts(account_indexes, 4)?;
            let burn_data: ConfidentialBurnInstructionData =
                *decode_instruction_data(instruction_data).map_err(|_| {
                    ParseInstructionError::InstructionNotParsable(ParsableProgram::SplToken)
                })?;
            let mut value = json!({
                "account": account_keys[account_indexes[0] as usize].to_string(),
                "mint": account_keys[account_indexes[1] as usize].to_string(),
                "newDecryptableAvailableBalance": burn_data.new_decryptable_available_balance.to_string(),
                "equalityProofInstructionOffset": burn_data.equality_proof_instruction_offset,
                "ciphertextValidityProofInstructionOffset": burn_data.ciphertext_validity_proof_instruction_offset,
                "rangeProofInstructionOffset": burn_data.range_proof_instruction_offset,
            });
```

**File:** transaction-status/src/parse_token/extension/permissioned_burn.rs (L104-121)
```rust
            let map = value.as_object_mut().unwrap();
            // The permissioned burn authority and the owner/delegate are the
            // trailing accounts; everything between the mint and them is optional
            // proof material. Reserve those two when walking the proof accounts.
            let mut offset = 2;
            let has_sysvar = burn_data.equality_proof_instruction_offset != 0
                || burn_data.ciphertext_validity_proof_instruction_offset != 0
                || burn_data.range_proof_instruction_offset != 0;

            // We use `saturating_sub(2)` because the permissioned burn authority
            // and the owner/delegate are always the trailing 2+ accounts.
            if has_sysvar && offset < account_indexes.len().saturating_sub(2) {
                map.insert(
                    "instructionsSysvar".to_string(),
                    json!(account_keys[account_indexes[offset] as usize].to_string()),
                );
                offset += 1;
            }
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
