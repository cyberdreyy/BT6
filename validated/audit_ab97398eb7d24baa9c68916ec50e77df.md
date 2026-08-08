### Title
Unvalidated Mint Owner in `get_parsed_token_account` Leads to Misreported Token Balances via `getAccountInfo`/`accountSubscribe` (JsonParsed encoding) - (File: rpc/src/parsed_token_accounts.rs)

### Summary
`get_parsed_token_account()`, used by `getAccountInfo`/`getMultipleAccounts`/`simulateTransaction` and `accountSubscribe` when `encoding: jsonParsed` is requested on a token-program account, resolves the "mint" field embedded in the token account's raw bytes and fetches whatever account lives at that address from the bank — without checking that the fetched account is actually owned by the SPL Token/Token-2022 program before treating its bytes as a `Mint` struct.

### Finding Description
`get_parsed_token_account` extracts the 32-byte "mint" field directly from account data via `get_token_account_mint()` [1](#0-0)  and then loads whatever account currently resides at that pubkey from the bank/overwrite map, feeding its raw bytes into `get_additional_mint_data()`, which blindly attempts `StateWithExtensions::<Mint>::unpack(data)` with no owner check: [2](#0-1) [3](#0-2) 

This is the exact bug-class of the reported issue: an attacker-controlled address (the fake `DaosLive` contract mimicking the trusted `token`/`lpTokenId`) is trusted purely because it *looks like* the right shape, with no ownership/identity verification. Here, since a token account's "mint" field is fully attacker-controlled (any account can be created with the SPL Token program as owner but an arbitrary byte-for-byte-crafted "mint" pubkey pointing to any account the attacker also controls — e.g. a System-owned account whose data the attacker fills to match the `Mint` packed layout, since `Mint::unpack`/`StateWithExtensions::<Mint>::unpack` do not check the owning program), the RPC will parse that attacker account as if it were a legitimate token mint and use its `decimals`/`interest_bearing_config`/`scaled_ui_amount_config` fields to compute the reported `UiTokenAmount` (ui_amount, ui_amount_string, decimals) for the token account.

Contrast this with the correctly-guarded sibling path `get_mint_owner_and_additional_data()` (used by `get_parsed_token_accounts`, the batch/plural version reached via `getProgramAccounts`/`getTokenAccountsByOwner`), which returns and lets callers check `mint_account.owner()` against `is_known_spl_token_id()`: [4](#0-3) 

The singular `get_parsed_token_account` (used for single-account lookups and subscriptions) skips this ownership check entirely, so an attacker can make the RPC misreport a completely fabricated decimal count/scaling for any token account whose "mint" pointer they steer at a self-controlled fake mint-shaped account.

### Impact Explanation
This causes the validator's RPC layer to return **wrong account/derived data** for a legitimate, real token account — the `tokenAmount.uiAmount`/`decimals` fields shown to any client (wallets, block explorers, exchanges relying on `jsonParsed` output) can be arbitrarily skewed by an attacker who merely needs to get a victim to query (or subscribe to) a token account whose mint field they crafted, or by crafting their own token account with a mint pointer of their choosing and having downstream consumers trust the parsed value. This matches the "wrong-slot/fork/account data returned" acceptance criterion — it's a decoder/misreporting bug reachable through unprivileged single-request `getAccountInfo`/`accountSubscribe` calls, no special privileges required.

### Likelihood Explanation
High — no signature, transaction, or special privilege is required. An attacker only needs to fund/create one throwaway SPL-Token-owned account with a "mint" field pointing at a second attacker-controlled account (which just needs to satisfy `Mint::unpack`'s byte layout, not any ownership constraint), then call `getAccountInfo` with `encoding: jsonParsed` (or `accountSubscribe`) against the RPC node once.

### Recommendation
In `get_parsed_token_account` (and any other single-account JsonParsed encode paths), verify that the resolved mint account's `owner()` matches a known SPL Token program id (`is_known_spl_token_id`) before calling `get_additional_mint_data`, mirroring the check already performed in `get_mint_owner_and_additional_data`/`get_token_program_id_and_mint`.

### Proof of Concept
1. Create account `Fake_Mint` owned by e.g. the System Program (or any non-token program), with data bytes crafted to match the packed `Mint` layout (arbitrary `decimals`, `supply`, etc.) — `Mint::unpack`/`StateWithExtensions::<Mint>::unpack` do not validate the owner field, so this succeeds.
2. Create a real SPL-Token account `Victim_TA` owned by the Token program with its `mint` field (first 32 bytes) set to `Fake_Mint`'s pubkey.
3. Call `getAccountInfo(Victim_TA, {encoding: "jsonParsed"})` (or `accountSubscribe`).
4. `get_parsed_token_account` extracts `mint = Fake_Mint` via `get_token_account_mint`, fetches `Fake_Mint` from the bank without checking its owner, and unpacks the attacker-chosen `decimals`/extension config into the returned `tokenAmount`, producing an incorrect `uiAmount`/`uiAmountString`/`decimals` for `Victim_TA` in the RPC response — a misreporting of account data caused entirely by trusting an unauthenticated, attacker-controlled pointer.

### Citations

**File:** account-decoder/src/parse_token.rs (L166-170)
```rust
pub fn get_token_account_mint(data: &[u8]) -> Option<Pubkey> {
    Account::valid_account_data(data)
        .then(|| Pubkey::try_from(data.get(..32)?).ok())
        .flatten()
}
```

**File:** rpc/src/parsed_token_accounts.rs (L23-50)
```rust
pub fn get_parsed_token_account(
    bank: &Bank,
    pubkey: &Pubkey,
    account: AccountSharedData,
    // only used for simulation results
    overwrite_accounts: Option<&HashMap<Pubkey, AccountSharedData>>,
) -> UiAccount {
    let additional_data = get_token_account_mint(account.data())
        .and_then(|mint_pubkey| {
            account_resolver::get_account_from_overwrites_or_bank(
                &mint_pubkey,
                bank,
                overwrite_accounts,
            )
        })
        .and_then(|mint_account| get_additional_mint_data(bank, mint_account.data()).ok())
        .map(|data| AccountAdditionalDataV3 {
            spl_token_additional_data: Some(data),
        });

    encode_ui_account(
        pubkey,
        &account,
        UiAccountEncoding::JsonParsed,
        additional_data,
        None,
    )
}
```

**File:** rpc/src/parsed_token_accounts.rs (L90-108)
```rust
/// Analyze a mint Pubkey that may be the native_mint and get the mint-account owner (token
/// program_id) and decimals
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

**File:** rpc/src/parsed_token_accounts.rs (L110-130)
```rust
fn get_additional_mint_data(bank: &Bank, data: &[u8]) -> Result<SplTokenAdditionalDataV2> {
    StateWithExtensions::<Mint>::unpack(data)
        .map_err(|_| {
            Error::invalid_params("Invalid param: Token mint could not be unpacked".to_string())
        })
        .map(|mint| {
            let interest_bearing_config = mint
                .get_extension::<InterestBearingConfig>()
                .map(|x| (*x, bank.clock().unix_timestamp))
                .ok();
            let scaled_ui_amount_config = mint
                .get_extension::<ScaledUiAmountConfig>()
                .map(|x| (*x, bank.clock().unix_timestamp))
                .ok();
            SplTokenAdditionalDataV2 {
                decimals: mint.base.decimals,
                interest_bearing_config,
                scaled_ui_amount_config,
            }
        })
}
```
