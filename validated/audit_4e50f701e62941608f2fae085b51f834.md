### Title
`parse_signers` infers multisig vs. single-key authority from instruction account-list length rather than actual on-chain account state, allowing attacker-controlled misreporting of token authority fields - ([File: transaction-status/src/parse_token.rs])

### Summary
`parse_signers` in `transaction-status/src/parse_token.rs` decides whether to emit `owner_field_name` (e.g. `"authority"`) or `multisig_field_name` (e.g. `"multisigAuthority"`) plus a fabricated `"signers"` array purely by comparing `accounts.len()` to `last_nonsigner_index + 1`, with no reference to the actual on-chain data of the referenced authority account. Since the trailing account count in a compiled instruction is entirely attacker-controlled at submission time, an attacker can force `getTransaction`/`getConfirmedTransaction` (jsonParsed) to report a real single-key authority as a multisig (with bogus "signers"), or a real multisig authority as a plain single-key authority.

### Finding Description
`parse_signers` is called from every relevant `TokenInstruction` arm (`Transfer`, `Approve`, `Revoke`, `SetAuthority`, `MintTo`, `Burn`, `CloseAccount`, `FreezeAccount`, `ThawAccount`, `TransferChecked`, `ApproveChecked`, `MintToChecked`, `BurnChecked`, `UnwrapLamports`, and various token-2022 extension parsers such as `memo_transfer` and `confidential_transfer`) with a fixed `last_nonsigner_index`: [1](#0-0) 

The logic is:
```
if accounts.len() > last_nonsigner_index + 1 {
    // treat accounts[last_nonsigner_index] as a multisig account
    // treat everything after it as "signers"
} else {
    // treat accounts[last_nonsigner_index] as a single-key owner/authority
}
```

The real SPL Token / Token-2022 program (`Processor::validate_owner`) determines multisig-ness by inspecting the *actual account data* at the owner position: if that account is owned by the token program and its data unpacks as `Multisig`, it is treated as a multisig and the program consumes as many of the *remaining* passed accounts as needed to satisfy `m` signers; extra unused trailing accounts are otherwise simply ignored by the program when the owner is a plain (non-multisig) account. This means the on-chain semantic of "is this a multisig authority" is governed by the referenced account's *data*, not by how many extra account indices the instruction happens to carry.

Because `parse_signers` uses the syntactic account-list length as a proxy for that semantic fact, an unprivileged attacker who fully controls the account list of a self-submitted instruction can decouple the two:

- **Single-key authority reported as multisig:** Use a real, ordinary (non-multisig) owner account for e.g. `MintTo` (`last_nonsigner_index = 2`), but append one or more arbitrary extra account indices after it. On-chain, the token program ignores the unused trailing accounts and the instruction can execute successfully (owner is a plain signer). The parser, seeing `accounts.len() > 3`, emits `"multisigMintAuthority"` and a fabricated `"signers"` list built from the attacker-chosen trailing indices, even though those accounts never functioned as multisig signers and the true owner is not a multisig at all.
- **Multisig authority reported as single-key:** Reference a real, previously initialized `Multisig` account as the owner but omit the trailing signer accounts (list length exactly `last_nonsigner_index + 1`). The parser reports plain `"mintAuthority"`/`"authority"` even though the referenced account is genuinely a multisig. (This transaction will fail on-chain for lack of sufficient multisig signers, but failed transactions are still recorded in the ledger and returned with parsed instruction data by `getTransaction`.)

Both scenarios are reachable purely by an unprivileged client crafting and submitting one transaction and later querying it via `getTransaction`/`getConfirmedTransaction` with `jsonParsed` encoding — no special privilege, mocked paths, or leaked keys are needed. Corresponding unit tests in the same file demonstrate the length-based branching is the sole determinant of the emitted field name: [2](#0-1) [3](#0-2) .

### Impact Explanation
This falls squarely in the described scope: an unprivileged attacker can cause `getTransaction`/`getConfirmedTransaction` (jsonParsed) to misreport the `authority`/`multisigAuthority`/`signers`/`owner`/`multisigOwner` fields for SPL Token instructions, misleading downstream integrators (explorers, indexers, compliance tools) about who actually authorized a mint/burn/transfer/approve and whether a multisig was involved. This is a decoder misreporting issue (wrong parsed metadata returned for a real, attacker-crafted transaction), not a validator crash or consensus issue.

### Likelihood Explanation
Fully attacker-controlled and repeatable: the attacker only needs to construct their own transaction (with a self-chosen account list shape) and submit it once; for the "single-key reported as multisig" variant the transaction can succeed normally, so no special preconditions beyond normal fee-payer/signature requirements are needed. Any client can then retrieve the misreported metadata via one `getTransaction` call.

### Recommendation
`parse_signers` (and all extension-specific equivalents) should not infer multisig-ness from account-list length alone. Where feasible, cross-check against the actual account data (e.g., via account-state fetch used elsewhere in `account-decoder/src/parse_token.rs` for `Multisig` unpacking) before choosing the `owner_field_name` vs. `multisig_field_name` branch, or otherwise clearly document/label the output as a best-effort heuristic derived from instruction shape rather than authoritative on-chain multisig status, so integrators do not treat it as ground truth.

### Proof of Concept
Rust integration test sketch, added to `transaction-status/src/parse_token.rs` tests module:
```rust
#[test]
fn test_parse_signers_length_heuristic_mismatch() {
    let program_id = spl_token_2022_interface::id();
    let real_owner = Pubkey::new_unique(); // ordinary key, NOT a multisig
    let mint = Pubkey::new_unique();
    let account = Pubkey::new_unique();
    let bogus_extra = Pubkey::new_unique(); // arbitrary key, never a real signer

    // Build a MintTo instruction with the minimum accounts, then manually
    // append a bogus extra account index to simulate attacker-controlled shape.
    let mint_to_ix = mint_to(&program_id, &mint, &account, &real_owner, &[], 100).unwrap();
    let mut message = Message::new(&[mint_to_ix], None);
    // account_keys: [payer?, mint, account, real_owner, ...]; append bogus_extra
    message.account_keys.push(bogus_extra);
    let compiled = &mut message.instructions[0];
    let bogus_index = (message.account_keys.len() - 1) as u8;
    compiled.accounts.push(bogus_index);

    let parsed = parse_token(compiled, &AccountKeys::new(&message.account_keys, None)).unwrap();
    let info = parsed.info.as_object().unwrap();

    // Parser incorrectly reports this ordinary owner as a multisig authority,
    // even though `real_owner` is not a Multisig account on-chain.
    assert!(info.contains_key("multisigMintAuthority"));
    assert_eq!(
        info["multisigMintAuthority"],
        json!(real_owner.to_string())
    );
    assert_eq!(info["signers"], json!(vec![bogus_extra.to_string()]));
    assert!(!info.contains_key("mintAuthority"));
}
```
Expected assertion failure demonstrates the mismatch: the emitted JSON claims `real_owner` is a multisig authority backed by `bogus_extra` as a signer, despite `real_owner` being an ordinary single-key account and `bogus_extra` never having participated as an actual multisig signer.

### Citations

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

**File:** transaction-status/src/parse_token.rs (L1197-1231)
```rust
        // Test Transfer, incl multisig
        let recipient = Pubkey::new_unique();
        #[allow(deprecated)]
        let transfer_ix =
            transfer(program_id, &account_pubkey, &recipient, &owner, &[], 42).unwrap();
        let message = Message::new(&[transfer_ix], None);
        let compiled_instruction = &message.instructions[0];
        assert_eq!(
            parse_token(
                compiled_instruction,
                &AccountKeys::new(&message.account_keys, None)
            )
            .unwrap(),
            ParsedInstructionEnum {
                instruction_type: "transfer".to_string(),
                info: json!({
                    "source": account_pubkey.to_string(),
                    "destination": recipient.to_string(),
                    "authority": owner.to_string(),
                    "amount": "42",
                })
            }
        );

        #[allow(deprecated)]
        let transfer_ix = transfer(
            program_id,
            &account_pubkey,
            &recipient,
            &multisig_pubkey,
            &[&multisig_signer0, &multisig_signer1],
            42,
        )
        .unwrap();
        let message = Message::new(&[transfer_ix], None);
```

**File:** transaction-status/src/parse_token.rs (L1547-1585)
```rust
        let transfer_ix = transfer_checked(
            program_id,
            &account_pubkey,
            &mint_pubkey,
            &recipient,
            &multisig_pubkey,
            &[&multisig_signer0, &multisig_signer1],
            42,
            2,
        )
        .unwrap();
        let message = Message::new(&[transfer_ix], None);
        let compiled_instruction = &message.instructions[0];
        assert_eq!(
            parse_token(
                compiled_instruction,
                &AccountKeys::new(&message.account_keys, None)
            )
            .unwrap(),
            ParsedInstructionEnum {
                instruction_type: "transferChecked".to_string(),
                info: json!({
                    "source": account_pubkey.to_string(),
                    "destination": recipient.to_string(),
                    "mint": mint_pubkey.to_string(),
                    "multisigAuthority": multisig_pubkey.to_string(),
                    "signers": vec![
                        multisig_signer0.to_string(),
                        multisig_signer1.to_string(),
                    ],
                    "tokenAmount": {
                        "uiAmount": 0.42,
                        "decimals": 2,
                        "amount": "42",
                        "uiAmountString": "0.42",
                   }
                })
            }
        );
```
