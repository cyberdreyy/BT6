### Title
`parse_token`'s `SetAuthority` handler labels `account_keys[0]` as "mint" or "account" purely from `authority_type`, without validating the referenced account's actual on-chain type - (File: `transaction-status/src/parse_token.rs`)

### Summary
The JSON-RPC transaction parser for SPL Token's `SetAuthority` instruction determines whether to emit the target account under the `"mint"` or `"account"` JSON key solely by pattern-matching the `authority_type` field taken from instruction data, without ever reading or validating the actual account state referenced by `instruction.accounts[0]`. An attacker can submit a `SetAuthority` instruction with an `authority_type` variant that maps to `"mint"` (e.g. `AuthorityType::MintTokens`) while `account_keys[0]` actually points to a token account (not a mint), causing `getTransaction`/`getConfirmedTransaction` (jsonParsed encoding) to report a misleading label for the affected account.

### Finding Description
In `parse_token` at [1](#0-0) , the `TokenInstruction::SetAuthority` arm computes the `owned` key string exclusively from the `authority_type` enum value decoded from instruction data:
```
let owned = match authority_type {
    AuthorityType::MintTokens | ... => "mint",
    AuthorityType::AccountOwner | AuthorityType::CloseAccount => "account",
};
let mut value = json!({
    owned: account_keys[instruction.accounts[0] as usize].to_string(),
    ...
});
```
No account data is fetched or inspected anywhere in `parse_token` — the function only receives `instruction: &CompiledInstruction` and `account_keys: &AccountKeys` [2](#0-1) , both of which are purely instruction/message-level metadata with no account-state binding. Consequently, if `account_keys[0]` is actually a token account rather than a mint (or vice versa), the parser will still tag it `"mint"` whenever `authority_type` is one of the mint-authority variants, regardless of the real underlying account type. The on-chain SPL Token / Token-2022 program independently validates the account's actual discriminant/type at execution time, so a semantic mismatch would cause the instruction to fail on-chain, but the transaction is still included in a block (as a failed transaction) and its instructions are still parsed into JSON exactly the same way by `parse_token`, since this parsing path has no dependency on execution success and no account-state validation.

### Impact Explanation
This falls under the accepted "decoder... misreporting" category: an RPC client fetching a transaction via `getTransaction` with `jsonParsed` encoding can be shown an account under the wrong semantic key (`mint` vs `account`), producing an inaccurate representation of which account/role was targeted by the instruction. This can mislead indexers, explorers, or wallets that trust the parsed JSON label to categorize the instruction (e.g., displaying "mint authority changed" for what was actually intended/executed against a token account, or vice versa) without independently decoding the raw instruction.

### Likelihood Explanation
Trivially reproducible by any unprivileged client: build any transaction containing a `SetAuthority` instruction where `instruction.accounts[0]` is a token account pubkey but `authority_type` is `MintTokens` (or another mint-labeled variant), submit it (it may fail on-chain, but that doesn't prevent inclusion/parsing), then call `getTransaction` with `jsonParsed` encoding. No special privileges, timing, or state are required beyond crafting the instruction bytes.

### Recommendation
`parse_token`'s account labeling for `SetAuthority` (and other instructions relying on authority_type/account-position heuristics) should either: (1) be documented as best-effort/purely-syntactic decoding that mirrors the raw instruction without account-state validation (matching the wider design of the entire `parse_token.rs` module, which never validates any accounts against fetched account state for any instruction type), or (2) if stronger guarantees are desired, the parser would need access to account state to cross-check the account discriminant before choosing the `owned` key — a much larger design change affecting the whole parsing architecture, not just this one instruction arm.

### Proof of Concept
```rust
// transaction-status/src/parse_token.rs (test module)
#[test]
fn test_parse_set_authority_mismatched_account_type() {
    // token_account_pubkey is a real *token account*, not a mint
    let token_account_pubkey = Pubkey::new_unique();
    let new_authority_pubkey = Pubkey::new_unique();
    let owner_pubkey = Pubkey::new_unique();

    let account_keys = AccountKeys::new(
        &[token_account_pubkey, owner_pubkey],
        None,
    );

    // authority_type = MintTokens even though accounts[0] is a token account
    let instruction = spl_token_2022_interface::instruction::set_authority(
        &spl_token_2022_interface::id(),
        &token_account_pubkey,
        Some(&new_authority_pubkey),
        AuthorityType::MintTokens,
        &owner_pubkey,
        &[],
    ).unwrap();

    let compiled = CompiledInstruction::new_from_raw_parts(
        0,
        instruction.data,
        vec![0, 1],
    );

    let parsed = parse_token(&compiled, &account_keys).unwrap();
    let info = parsed.info.as_object().unwrap();

    // BUG: labeled "mint" even though token_account_pubkey is actually a token account
    assert!(info.contains_key("mint"));
    assert_eq!(info["mint"], token_account_pubkey.to_string());
    assert!(!info.contains_key("account"));
}
```
Expected: test passes today, demonstrating that the parser blindly trusts `authority_type` to label the account role without any validation against the actual on-chain account type, confirming the misreporting behavior described.

### Citations

**File:** transaction-status/src/parse_token.rs (L30-33)
```rust
pub fn parse_token(
    instruction: &CompiledInstruction,
    account_keys: &AccountKeys,
) -> Result<ParsedInstructionEnum, ParseInstructionError> {
```

**File:** transaction-status/src/parse_token.rs (L220-248)
```rust
            TokenInstruction::SetAuthority {
                authority_type,
                new_authority,
            } => {
                check_num_token_accounts(&instruction.accounts, 2)?;
                let owned = match authority_type {
                    AuthorityType::MintTokens
                    | AuthorityType::FreezeAccount
                    | AuthorityType::TransferFeeConfig
                    | AuthorityType::WithheldWithdraw
                    | AuthorityType::CloseMint
                    | AuthorityType::InterestRate
                    | AuthorityType::PermanentDelegate
                    | AuthorityType::ConfidentialTransferMint
                    | AuthorityType::TransferHookProgramId
                    | AuthorityType::ConfidentialTransferFeeConfig
                    | AuthorityType::MetadataPointer
                    | AuthorityType::GroupPointer
                    | AuthorityType::GroupMemberPointer
                    | AuthorityType::ScaledUiAmount
                    | AuthorityType::Pause
                    | AuthorityType::PermissionedBurn => "mint",
                    AuthorityType::AccountOwner | AuthorityType::CloseAccount => "account",
                };
                let mut value = json!({
                    owned: account_keys[instruction.accounts[0] as usize].to_string(),
                    "authorityType": Into::<UiAuthorityType>::into(authority_type),
                    "newAuthority": map_coption_pubkey(new_authority),
                });
```
