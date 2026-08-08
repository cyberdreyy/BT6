### Title
`parse_signers` in `transaction-status/src/parse_token.rs` uses a purely count-based heuristic to decide multisig vs. single-authority, causing jsonParsed RPC output to misreport authority semantics for arbitrary attacker-crafted SPL Token instructions - ([File: transaction-status/src/parse_token.rs])

### Summary
`parse_signers` decides whether to emit `authority`/`owner` or `multisigAuthority`/`owner` plus a `signers` array solely by comparing `accounts.len()` to `last_nonsigner_index + 1`, with no verification that the account at `last_nonsigner_index` is actually a multisig account owned by the SPL Token program. An attacker can submit a syntactically valid (but semantically nonsensical/failing) Transfer/Approve/Burn/etc. instruction with one extra trailing account key, which the on-chain SPL Token program would either reject or simply ignore, yet the RPC's `jsonParsed` decoder will report it as a multisig authority with a fabricated `signers` list.

### Finding Description
`parse_signers` at [1](#0-0)  implements the branch purely on account-slice length:
```
if accounts.len() > last_nonsigner_index + 1 {
    // treat accounts[last_nonsigner_index] as multisigAuthority
    // treat accounts[last_nonsigner_index+1..] as "signers"
} else {
    // treat accounts[last_nonsigner_index] as plain authority/owner
}
```
This is invoked for `Transfer`, `Approve`, `Revoke`, `SetAuthority`, `MintTo`, `Burn`, `CloseAccount`, `FreezeAccount`, `ThawAccount`, `TransferChecked`, `ApproveChecked`, `MintToChecked`, `BurnChecked`, and multiple Token-2022 extension instructions (e.g., `parse_transfer_fee_instruction`, `parse_group_pointer_instruction`, `parse_interest_bearing_mint_instruction`, `parse_metadata_pointer_instruction`, `parse_permissioned_burn_instruction`), e.g. for `Transfer` at [2](#0-1) .

In the actual SPL Token/Token-2022 on-chain program, whether an authority is treated as a multisig is determined by the *account data* at that position (owned by the token program and matching the `Multisig` account layout), not by how many trailing accounts a client chose to include in the instruction's account list. An unprivileged attacker can build a `Transfer` instruction where `accounts = [source, destination, authority_X, Y]`, with `authority_X` being an ordinary wallet (not a real multisig account) and `Y` an arbitrary pubkey the attacker controls (need not even be a real signer of anything). This transaction will fail on-chain execution (the real program will reject it, e.g. with invalid account data, or simply ignore `Y` since the multisig-check path is data-gated), but a failed transaction is still included and finalized in the ledger. Any subsequent `getTransaction` call with `jsonParsed` encoding will run this instruction through `parse_token::parse_signers`, which — seeing 4 accounts vs. the expected minimum of 3 — unconditionally emits `"multisigAuthority": authority_X` and `"signers": ["Y"]`, misrepresenting `authority_X` as a multisig owner and fabricating a false signer relationship for `Y`, even though on-chain semantics never established such a relationship (the instruction failed, or `Y` was never validated as a signer of anything).

No account-data check, execution-outcome check, or program-side validation gates this parsing path; it only depends on the raw instruction bytes/account list that the attacker fully controls when constructing their own transaction.

### Impact Explanation
This falls under the disclosed "misreport program, authority, amount, decimals, or token owner to downstream integrators" bounty category for `svm`/`transaction-status` decoding scope. Any wallet, explorer, indexer, or compliance tool that trusts Agave's `jsonParsed` RPC output for token instructions can be misled into believing a plain wallet is a multisig authority and that an unrelated pubkey participated as a signer, which can affect authorization-provenance displays, audit trails, and automated policy decisions built on top of `getTransaction`/`getConfirmedTransaction` parsed output.

### Likelihood Explanation
Fully feasible with a single unprivileged client: the attacker only needs to submit one transaction (which may even fail execution — failed transactions are still finalized and their instructions still parsed) and then issue one `getTransaction` RPC call. No special privileges, staking, or leader/validator control is required, satisfying the single-call-rate constraint. This is trivially repeatable for every affected instruction variant (`Transfer`, `Approve`, `Burn`, `SetAuthority`, extension instructions, etc.).

### Recommendation
Do not infer multisig-ness purely from account-list length in `parse_signers`. Either (a) clearly document/limit the parsed output's guarantee as "positional heuristic, not validated against account state" so integrators do not treat `multisigAuthority`/`signers` as verified on-chain facts, or (b) have the parser cross-check the referenced account's owner and data length against the `Multisig` layout (requires account-state lookup, which the current stateless instruction parser does not have access to) before emitting the multisig fields, falling back to a neutral representation (e.g., include all trailing accounts as an untyped `additionalAccounts` field) when such verification isn't possible.

### Proof of Concept
```rust
// transaction-status/src/parse_token.rs (unit test)
#[test]
fn test_parse_signers_count_based_heuristic_misreports_authority() {
    use solana_message::AccountKeys;
    use serde_json::{json, Map};

    let keys = vec![
        Pubkey::new_unique(), // source
        Pubkey::new_unique(), // destination
        Pubkey::new_unique(), // authority_X (ordinary wallet, NOT a real multisig)
        Pubkey::new_unique(), // Y (arbitrary attacker-chosen key, not a real signer of anything)
    ];
    let account_keys = AccountKeys::new(&keys, None);
    let accounts: Vec<u8> = vec![0, 1, 2, 3]; // Transfer: last_nonsigner_index = 2

    let mut value = json!({});
    let map = value.as_object_mut().unwrap();
    parse_signers(map, 2, &account_keys, &accounts, "authority", "multisigAuthority");

    // Bug: authority_X is misreported as a multisig authority, and Y is
    // fabricated as a "signer", even though authority_X is an ordinary
    // wallet and Y has no real signing relationship with it.
    assert_eq!(
        map.get("multisigAuthority").unwrap(),
        &json!(keys[2].to_string())
    );
    assert_eq!(map.get("signers").unwrap(), &json!([keys[3].to_string()]));
    assert!(map.get("authority").is_none());
}
```
Expected assertion: the test demonstrates that `parse_signers` always classifies `authority_X` as `multisigAuthority` and `Y` as a `signer` whenever an extra trailing account is present, with no verification against actual on-chain `Multisig` account data — confirming the field-naming misrepresentation is reachable purely from attacker-controlled instruction construction.

### Citations

**File:** transaction-status/src/parse_token.rs (L159-178)
```rust
            TokenInstruction::Transfer { amount } => {
                check_num_token_accounts(&instruction.accounts, 3)?;
                let mut value = json!({
                    "source": account_keys[instruction.accounts[0] as usize].to_string(),
                    "destination": account_keys[instruction.accounts[1] as usize].to_string(),
                    "amount": amount.to_string(),
                });
                let map = value.as_object_mut().unwrap();
                parse_signers(
                    map,
                    2,
                    account_keys,
                    &instruction.accounts,
                    "authority",
                    "multisigAuthority",
                );
                Ok(ParsedInstructionEnum {
                    instruction_type: "transfer".to_string(),
                    info: value,
                })
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
