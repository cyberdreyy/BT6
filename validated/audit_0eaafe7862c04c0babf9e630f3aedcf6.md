### Title
ConfidentialBurn decoder mislabels attacker-chosen account as `permissionedBurnAuthority` when account list is shorter than what the encoded proof offsets require - (File: transaction-status/src/parse_token/extension/permissioned_burn.rs)

### Summary
`parse_permissioned_burn_instruction`'s `ConfidentialBurn` branch only enforces a generic minimum of 4 accounts via `check_num_token_accounts(account_indexes, 4)`, but does not validate that `account_indexes.len()` actually matches the number of accounts implied by the attacker-controlled `equality_proof_instruction_offset`/`ciphertext_validity_proof_instruction_offset`/`range_proof_instruction_offset` fields. With all three offsets set to zero and exactly 4 accounts supplied, the repeated `offset < account_indexes.len().saturating_sub(2)` guards cause every conditional proof/sysvar insertion to be skipped, and the index that should semantically represent a proof context account is instead labeled `"permissionedBurnAuthority"` in the JSON output.

### Finding Description
The relevant logic is: [1](#0-0) 

With `account_indexes.len() == 4` and `equality_proof_instruction_offset == ciphertext_validity_proof_instruction_offset == range_proof_instruction_offset == 0`:
- `has_sysvar` is `false`, so the sysvar insertion is skipped regardless.
- For each of the three `== 0` conditional blocks, the guard `offset < account_indexes.len().saturating_sub(2)` evaluates to `2 < 2` → `false`, so **none** of `equalityProofContextStateAccount`, `ciphertextValidityProofContextStateAccount`, or `rangeProofContextStateAccount` are ever inserted — even though offsets of `0` are the code's own sentinel value for "this proof is supplied via context-state account, not embedded in a sibling instruction," which implies these accounts *should* exist.
- `offset` remains at `2`. The final check `offset < account_indexes.len().saturating_sub(1)` (`2 < 3`) is `true`, so `account_indexes[2]` — which, per the offset semantics the attacker themselves encoded, was implicitly designated as the position of the first context-state account — is instead labeled `"permissionedBurnAuthority"`, and `account_indexes[3]` is passed into `parse_signers` as `"authority"`/`"multisigAuthority"`.

The root cause is that `check_num_token_accounts(account_indexes, 4)` at line 91 is a static minimum shared with `Burn`/`BurnChecked`, but the `ConfidentialBurn` variant's actual required account count is variable and depends on the proof-offset fields also decoded from the same instruction data (which are fully attacker-controlled, since this is raw, unvalidated transaction data being decoded for RPC display, not re-derived from any trusted source). Nothing in the code cross-checks `account_indexes.len()` against the number of accounts implied by the offsets before doing positional interpretation, so a short account list silently degrades into a different (attacker-influenceable) accounting for which position becomes `permissionedBurnAuthority` vs. a proof-context account vs. the signer authority.

This is a decoder-only bug in `transaction-status`; it is not part of the sBPF/on-chain execution path and does not affect consensus. It is reachable purely by an unprivileged client submitting a transaction containing a `ConfidentialBurn` instruction with these malformed field values, followed by a single `getTransaction`/`getConfirmedTransaction` (jsonParsed) RPC call.

### Impact Explanation
This falls under the "decoder panic and misreporting" acceptance category. The practical impact is limited to RPC/explorer-facing misreporting: a `getTransaction` (jsonParsed) response for such a crafted `ConfidentialBurn` instruction will label an account as `"permissionedBurnAuthority"` that does not correspond to the position a well-formed instruction (matching the declared zero-offset/context-account mode) would use for that role, while silently dropping the `*ProofContextStateAccount` fields entirely. A downstream integrator that trusts the parsed field name (rather than re-deriving semantics from raw `account_indexes` and the actual spl-token-2022 program logic) could therefore attribute burn authority to the wrong account. Because the real spl-token-2022 program requires strictly more accounts for zero-offset ("context state account") proof mode than the 4-account minimum this decoder enforces, such a maliciously short instruction would typically fail on-chain execution; the misreport is confined to the parsed metadata shown for that (likely failed) transaction, not to any consensus-affecting state change.

### Likelihood Explanation
Fully attacker-controlled and reproducible with a single crafted transaction: the attacker only needs to build a `ConfidentialBurn` instruction with exactly 4 accounts and zero-valued proof offsets (both trivially settable in the raw instruction data) and submit it (e.g., via `sendTransaction`, optionally with `skipPreflight`), then call `getTransaction` once. No special permissions, staking, or timing constraints beyond the standard single-RPC-call budget are required.

### Recommendation
In the `ConfidentialBurn` branch, compute the exact expected account count from `has_sysvar` plus the number of proof offsets equal to zero (each requiring its own context-state account) plus the fixed trailing `permissionedBurnAuthority`/`authority` accounts, and validate `account_indexes.len()` equals that exact expected count (or return a decode error / omit fields consistently) before doing any positional indexing, instead of relying on the generic `saturating_sub(2)`/`saturating_sub(1)` heuristics against a loosely-checked minimum of 4.

### Proof of Concept
```rust
#[test]
fn test_confidential_burn_short_account_list_misattributes_authority() {
    use spl_token_2022_interface::extension::confidential_mint_burn::instruction::BurnInstructionData as ConfidentialBurnInstructionData;

    // Build a ConfidentialBurn instruction with 4 accounts and all proof
    // offsets == 0 (attacker-controlled, arbitrary pubkeys).
    let account = Pubkey::new_unique();
    let mint = Pubkey::new_unique();
    let would_be_proof_ctx = Pubkey::new_unique(); // attacker's chosen "index 2" account
    let owner = Pubkey::new_unique();

    // Construct raw instruction data with all offsets zero (pseudo-code,
    // depends on actual encoder helper / manual byte construction that
    // bypasses the builder's own account-count enforcement).
    let burn_data = ConfidentialBurnInstructionData {
        equality_proof_instruction_offset: 0,
        ciphertext_validity_proof_instruction_offset: 0,
        range_proof_instruction_offset: 0,
        ..Default::default()
    };
    let instruction_data = encode_confidential_burn_instruction(&burn_data);

    let account_indexes: Vec<u8> = vec![0, 1, 2, 3]; // exactly 4 accounts
    let account_keys = AccountKeys::new(&[account, mint, would_be_proof_ctx, owner], None);

    let parsed = parse_permissioned_burn_instruction(
        &instruction_data,
        &account_indexes,
        &account_keys,
    ).unwrap();

    // Bug: index 2 (semantically implied to be a proof context account by
    // the zero offsets) is reported as permissionedBurnAuthority, and none
    // of the *ProofContextStateAccount fields are present despite offsets == 0.
    assert!(parsed.info.get("equalityProofContextStateAccount").is_none());
    assert!(parsed.info.get("ciphertextValidityProofContextStateAccount").is_none());
    assert!(parsed.info.get("rangeProofContextStateAccount").is_none());
    assert_eq!(
        parsed.info["permissionedBurnAuthority"],
        json!(would_be_proof_ctx.to_string()) // mislabeled
    );
}
```
Expected fix behavior: the parser should either reject the instruction as malformed (account count inconsistent with declared proof offsets) or clearly omit `permissionedBurnAuthority` rather than assigning it to an account that the offset encoding designates as a proof-context slot.

### Citations

**File:** transaction-status/src/parse_token/extension/permissioned_burn.rs (L108-159)
```rust
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

            if burn_data.equality_proof_instruction_offset == 0
                && offset < account_indexes.len().saturating_sub(2)
            {
                map.insert(
                    "equalityProofContextStateAccount".to_string(),
                    json!(account_keys[account_indexes[offset] as usize].to_string()),
                );
                offset += 1;
            }

            if burn_data.ciphertext_validity_proof_instruction_offset == 0
                && offset < account_indexes.len().saturating_sub(2)
            {
                map.insert(
                    "ciphertextValidityProofContextStateAccount".to_string(),
                    json!(account_keys[account_indexes[offset] as usize].to_string()),
                );
                offset += 1;
            }

            if burn_data.range_proof_instruction_offset == 0
                && offset < account_indexes.len().saturating_sub(2)
            {
                map.insert(
                    "rangeProofContextStateAccount".to_string(),
                    json!(account_keys[account_indexes[offset] as usize].to_string()),
                );
                offset += 1;
            }

            if offset < account_indexes.len().saturating_sub(1) {
                map.insert(
                    "permissionedBurnAuthority".to_string(),
                    json!(account_keys[account_indexes[offset] as usize].to_string()),
                );
                offset += 1;
            }
```
