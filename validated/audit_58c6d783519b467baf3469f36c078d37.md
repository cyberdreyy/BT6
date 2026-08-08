## Finding

### Title
`get_parsed_token_accounts` decodes token balances using a "mint" account without verifying it is owned by a known SPL Token program - (File: `rpc/src/parsed_token_accounts.rs`)

### Summary
The external report describes a redeem function that accepts a token address parameter without checking it is actually one of the whitelisted tokens (wBTC/renBTC), letting a caller substitute an arbitrary asset. The same class of bug — trusting an address-derived value without validating what it actually points to/who owns it — exists in the Solana RPC token-account JSON-parsing path: the mint address embedded in a token account's data is used to fetch "mint" data for decimal/UI-amount computation, but the code never checks that the fetched account is actually owned by a recognized SPL Token program before trusting its contents.

### Finding Description
`get_parsed_token_accounts` in [1](#0-0)  extracts the `mint` pubkey embedded in a token account's raw bytes via `get_token_account_mint`, then calls `get_mint_owner_and_additional_data` and **discards the returned owner** (`let (_, data) = ...`), using only the decoded `SplTokenAdditionalDataV2` (decimals, interest-bearing config, scaled-UI config) to build the additional data used for encoding.

`get_mint_owner_and_additional_data` itself performs no ownership validation — it fetches whatever account exists at that pubkey and attempts to unpack it as a `Mint`, returning `*mint_account.owner()` purely for the caller to optionally check: [2](#0-1) .

Other call sites in `rpc/src/rpc.rs` correctly validate this owner before trusting the data, e.g. `get_token_largest_accounts` explicitly checks `is_known_spl_token_id(&mint_owner)` [3](#0-2)  and `get_token_program_id_and_mint` (used by `getTokenAccountsByOwner`/`getTokenAccountsByDelegate` for filtering) does the same [4](#0-3) . However, `get_parsed_token_accounts` — which is invoked by both `get_token_accounts_by_owner` and `get_token_accounts_by_delegate` when `encoding == JsonParsed` [5](#0-4) [6](#0-5)  — skips this check entirely.

Because the `mint` field is only guaranteed valid at the time a token account is initialized, an account previously reused as a legitimate Mint can later be closed (e.g., Token-2022's `MintCloseAuthority` extension permits closing a zero-supply mint and reclaiming its lamports) and a completely unrelated account can subsequently be created at that same address by anyone. If that unrelated account's raw bytes happen to unpack successfully as a `Mint` struct (attacker-controlled data, since the new account can be created with arbitrary content), `get_parsed_token_accounts` will use its `decimals`/`interest_bearing_config`/`scaled_ui_amount_config` fields for stale token accounts still referencing that old address — without ever checking the new account's owner is actually a Token program.

### Impact Explanation
This causes `getTokenAccountsByOwner`/`getTokenAccountsByDelegate` (and `getProgramAccounts` with `jsonParsed` encoding, which shares the same helper) to return incorrect `tokenAmount.decimals`, `uiAmount`, and `uiAmountString` fields for a token account, sourced from data the caller does not control being the genuine mint. This is a "wrong account data returned" condition served to any unprivileged JSON-RPC caller, matching the accepted impact category of wrong-data-returned from a query.

### Likelihood Explanation
Reaching the mismatched-data state requires the mint's original owner to close/vacate its address (feasible today via the Token-2022 `MintCloseAuthority` extension, which is a shipped, non-experimental feature) and a new account subsequently occupying that same pubkey with data that happens to parse as a `Mint`. This is a concrete, non-mocked path through production RPC code reachable by any unprivileged client issuing a single `jsonParsed`-encoded query; it is not a theoretical-only scenario, though it depends on a specific account-reuse sequence occurring first.

### Recommendation
In `get_parsed_token_accounts` (and any other caller of `get_mint_owner_and_additional_data`), validate the returned mint-account owner with `is_known_spl_token_id(&mint_owner)` before using the derived `SplTokenAdditionalDataV2`, mirroring the check already performed in `get_token_largest_accounts` and `get_token_program_id_and_mint`, and skip/omit additional data (falling back to raw/no decimals) when the owner check fails.

### Proof of Concept
1. Create a Token-2022 mint `M` with the `MintCloseAuthority` extension and some `decimals` value; create a token account `A` (owned by the Token-2022 program) with `mint = M`.
2. Reduce `M`'s supply to zero and close it via the `MintCloseAuthority` extension, freeing the account and its lamports (address `M` now has zero lamports / no account).
3. Have any party create a new, unrelated account at address `M` (any owner) whose data byte-layout happens to unpack successfully as a `Mint` (e.g. by controlling account size/content through a program that writes attacker-chosen bytes), with a different `decimals` value.
4. Call `getTokenAccountsByOwner`/`getProgramAccounts` for `A`'s owner with `{"encoding":"jsonParsed"}`.
5. Observe that `get_parsed_token_accounts` [7](#0-6)  reports `A`'s balance using the new, unrelated account's decoded decimals instead of failing or reporting raw/no decimals, since the owner of the fetched "mint" account is never checked.

### Citations

**File:** rpc/src/parsed_token_accounts.rs (L52-70)
```rust
pub fn get_parsed_token_accounts<I>(
    bank: Arc<Bank>,
    keyed_accounts: I,
) -> impl Iterator<Item = RpcKeyedAccount>
where
    I: Iterator<Item = (Pubkey, AccountSharedData)>,
{
    let mut mint_data: HashMap<Pubkey, AccountAdditionalDataV3> = HashMap::new();
    keyed_accounts.filter_map(move |(pubkey, account)| {
        let additional_data = get_token_account_mint(account.data()).and_then(|mint_pubkey| {
            mint_data.get(&mint_pubkey).cloned().or_else(|| {
                let (_, data) = get_mint_owner_and_additional_data(&bank, &mint_pubkey).ok()?;
                let data = AccountAdditionalDataV3 {
                    spl_token_additional_data: Some(data),
                };
                mint_data.insert(mint_pubkey, data);
                Some(data)
            })
        });
```

**File:** rpc/src/parsed_token_accounts.rs (L92-108)
```rust
pub(crate) fn get_mint_owner_and_additional_data(
    bank: &Bank,
    mint: &Pubkey,
) -> Result<(Pubkey, SplTokenAdditionalDataV2)> {
    if mint == &spl_token_interface::native_mint::id() {
        Ok((
            spl_token_interface::id(),
            SplTokenAdditionalDataV2::with_decimals(spl_token_interface::native_mint::DECIMALS),
        ))
    } else {
        let mint_account = bank.get_account(mint).ok_or_else(|| {
            Error::invalid_params("Invalid param: could not find mint".to_string())
        })?;
        let mint_data = get_additional_mint_data(bank, mint_account.data())?;
        Ok((*mint_account.owner(), mint_data))
    }
}
```

**File:** rpc/src/rpc.rs (L2082-2087)
```rust
        let (mint_owner, data) = get_mint_owner_and_additional_data(&bank, &mint)?;
        if !is_known_spl_token_id(&mint_owner) {
            return Err(Error::invalid_params(
                "Invalid param: not a Token mint".to_string(),
            ));
        }
```

**File:** rpc/src/rpc.rs (L2170-2171)
```rust
        let accounts = if encoding == UiAccountEncoding::JsonParsed {
            get_parsed_token_accounts(bank.clone(), keyed_accounts.into_iter()).collect()
```

**File:** rpc/src/rpc.rs (L2236-2237)
```rust
        let accounts = if encoding == UiAccountEncoding::JsonParsed {
            get_parsed_token_accounts(bank.clone(), keyed_accounts.into_iter()).collect()
```

**File:** rpc/src/rpc.rs (L2705-2720)
```rust
/// Analyze a passed Pubkey that may be a Token program id or Mint address to determine the program
/// id and optional Mint
fn get_token_program_id_and_mint(
    bank: &Bank,
    token_account_filter: TokenAccountsFilter,
) -> Result<(Pubkey, Option<Pubkey>)> {
    match token_account_filter {
        TokenAccountsFilter::Mint(mint) => {
            let (mint_owner, _) = get_mint_owner_and_additional_data(bank, &mint)?;
            if !is_known_spl_token_id(&mint_owner) {
                return Err(Error::invalid_params(
                    "Invalid param: not a Token mint".to_string(),
                ));
            }
            Ok((mint_owner, Some(mint)))
        }
```
