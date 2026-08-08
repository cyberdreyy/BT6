### Title
`parse_signers` misreports single-owner Transfer/Approve/Burn/MintTo/SetAuthority instructions as multisig-authorized when trailing unused account indices are appended - (File: transaction-status/src/parse_token.rs)

### Summary
`parse_signers` (transaction-status/src/parse_token.rs:937-961) decides whether to emit `owner_field_name` or `multisig_field_name` + a synthesized `signers` array purely by comparing `accounts.len()` to `last_nonsigner_index + 1`, with no verification that the "owner" account is actually an spl-token `Multisig` account. Because the real spl-token program only reads the fixed set of accounts it needs (source/destination/owner) for a regular (non-multisig) authority and silently ignores any additional trailing account indices in the `CompiledInstruction`, an attacker can submit an otherwise-valid, successfully-executing Transfer/Approve/Burn/MintTo/SetAuthority instruction with one or more extra, arbitrary account indices appended after the real owner index. `getTransaction` with `jsonParsed` encoding will then run this data through `parse_signers`, which sees `accounts.len() > last_nonsigner_index + 1` and fabricates a `multisig...` field plus a `signers` array from the attacker-chosen trailing indices, even though on-chain the transfer was authorized by a single ordinary keypair.

### Finding Description
`parse_signers` is invoked from each of the Transfer/Approve/Burn/MintTo/SetAuthority (and their `Checked` variants) branches in `parse_token.rs` with `last_nonsigner_index` set to the last account position the instruction structurally requires (e.g., index 2 for `source, destination, authority`). Its entire logic is: [1](#0-0) 

```
if accounts.len() > last_nonsigner_index + 1 {
    // treat trailing accounts as multisig signers
} else {
    // treat accounts[last_nonsigner_index] as sole owner
}
```

This mirrors, but does not actually re-verify, the real spl-token program's runtime behavior. On-chain, spl-token's instruction processor determines multisig status by checking the *owner* account's actual `Account.owner` field / data (i.e., whether the account at the "authority" position is owned by the SPL Token Multisig program), not by counting how many account indices were passed in the `CompiledInstruction`. For an ordinary (non-multisig) owner, the token program reads only the accounts it needs; extra trailing `AccountInfo`s supplied in the instruction's account list are never consumed and do not cause the instruction to fail.

Consequently, an attacker who controls the account-index list of their own transaction can craft a `Transfer` instruction where the owner is a regular keypair, but append one or more arbitrary extra account indices (duplicates, unrelated read-only accounts, etc.) after the owner position. The instruction still executes successfully on-chain because spl-token ignores the unused indices. When this confirmed transaction is later fetched via `getTransaction` (jsonParsed encoding), `parse_signers` sees `accounts.len() > last_nonsigner_index + 1`, and:
- emits `multisigAuthority` (or equivalent) referencing `account_keys[accounts[last_nonsigner_index]]` instead of the correct `authority` field name, and
- fabricates a `signers` list from the attacker's trailing indices, which have no cryptographic relationship to actual multisig authorization.

There is no check anywhere in this function (or its callers) that cross-references the account's owner program or the actual `Multisig` account data; the branch is chosen solely by array length. This is a genuine decoder misreporting bug: the JSON-parsed output does not faithfully reflect the authorization model actually enforced on-chain by the real spl-token program.

### Impact Explanation
This falls under the "decoder panic and misreporting" accepted impact category. A downstream integrator (wallet, explorer, indexer, compliance/monitoring tool) relying on `jsonParsed` output from `getTransaction` would incorrectly conclude that a transfer was authorized by an M-of-N multisig with a specific `signers` set, when in fact it was a simple single-key-signed transfer. This is a data-integrity/misrepresentation issue in the RPC decoding layer, not a validator crash or consensus issue, but it directly violates the stated invariant that "parsed output faithfully represents raw instruction."

### Likelihood Explanation
Fully attacker-controlled and trivially reproducible: any unprivileged client can construct and submit (or have submitted) a transaction whose `CompiledInstruction` for a Transfer/Approve/Burn/MintTo/SetAuthority contains one extra, unused trailing account index after the real owner index. The spl-token program accepts such a transaction without any special condition (no elevated privilege, no validator control needed), and a single subsequent `getTransaction` call with `jsonParsed` encoding triggers the misreporting deterministically every time.

### Recommendation
`parse_signers` (and its callers) should not infer multisig-vs-single-owner status from the account count alone. Instead, the parser should determine multisig status the same way the on-chain program does — e.g., by checking whether the account at `last_nonsigner_index` is owned by the SPL Token/Token-2022 multisig-owning program and matches actual `Multisig` account structure/state, or, if such state isn't available to the parser, avoid asserting a `signers` list with confidence and instead treat any extra trailing accounts as "additional accounts (unverified)" rather than authoritative multisig signer data. At minimum, document/label the field to make clear it is inferred from account-list shape, not verified on-chain multisig state, to avoid downstream misattribution.

### Proof of Concept
```rust
#[test]
fn test_parse_signers_misreports_single_owner_as_multisig() {
    use {
        super::*, solana_instruction::Instruction, solana_message::Message,
        solana_pubkey::Pubkey, spl_token_2022_interface::instruction::transfer,
    };

    let program_id = spl_token_2022_interface::id();
    let source = Pubkey::new_unique();
    let destination = Pubkey::new_unique();
    let owner = Pubkey::new_unique(); // ordinary keypair, NOT a Multisig account
    let bogus_extra_account = Pubkey::new_unique(); // attacker-appended, unused by program

    // Build a legitimate single-owner Transfer instruction.
    let mut ix = transfer(&program_id, &source, &destination, &owner, &[], 100).unwrap();

    // Attacker appends one extra, arbitrary account index that the real
    // spl-token Transfer processor never reads/uses for a non-multisig owner.
    ix.accounts.push(solana_instruction::AccountMeta::new_readonly(bogus_extra_account, false));

    let message = Message::new(&[ix], None);
    let compiled_ix = &message.instructions[0];
    let account_keys = AccountKeys::new(&message.account_keys, None);

    let parsed = parse_token(compiled_ix, &account_keys).unwrap();
    let parsed_json = parsed.parsed;

    // BUG: parser reports a fabricated multisig authority + signers list
    // for what was actually a single-owner-authorized transfer on-chain.
    assert!(parsed_json.get("multisigAuthority").is_none(),
        "parse_signers incorrectly emitted multisigAuthority for a single-owner transfer");
    assert_eq!(
        parsed_json.get("authority").unwrap().as_str().unwrap(),
        owner.to_string()
    );
}
```
Expected (buggy) behavior today: the assertion `multisigAuthority.is_none()` fails because `parse_signers` emits `multisigAuthority` = `bogus_extra_account`... actually pointing at `accounts[last_nonsigner_index]` (the real owner) and a `signers` array containing `bogus_extra_account`, while `authority` is absent — demonstrating the misreporting described.

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
