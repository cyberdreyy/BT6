## Title
Unvalidated `mint` field trusted during JSON-parsed token account decoding, allowing address/decimals spoofing in RPC responses - (File: `rpc/src/parsed_token_accounts.rs`)

## Summary
The reported bug is a "chain of origin" issue: the `redeem()` function trusts a caller-supplied `_fromChain` identifier without verifying it actually matches a supported/valid source. The closest reachable analog in this codebase is in `get_parsed_token_account()` / `get_parsed_token_accounts()` in `rpc/src/parsed_token_accounts.rs`: when decoding an SPL token account for `jsonParsed` encoding, the code extracts the embedded `mint` pubkey directly from account byte data via `get_token_account_mint()` and then fetches whatever account lives at that pubkey to derive `decimals`/extension config, without verifying that the fetched "mint" account is actually owned by a legitimate SPL Token/Token-2022 program.

## Finding Description
`get_token_account_mint()` in `account-decoder/src/parse_token.rs` only checks that the account's data blob has the correct *length/format* to be treated as a token `Account`; it does not verify anything about the account whose pubkey is embedded as the first 32 bytes of that data: [1](#0-0) 

That extracted `mint_pubkey` is then used to fetch a real bank/overwrite account and unconditionally treated as a `Mint`: [2](#0-1) [3](#0-2) 

The helper `get_mint_owner_and_additional_data()` fetches the account at `mint` from the bank and unpacks it as `Mint` — it never checks `mint_account.owner()` against `spl_token_interface::id()` / `spl_token_2022_interface::id()` before calling `StateWithExtensions::<Mint>::unpack`: [4](#0-3) 

The gating check that the *token account itself* is owned by a known SPL Token program (`is_known_spl_token_id(account.owner())`) happens one layer up in `rpc/src/rpc.rs` before calling into `get_parsed_token_account`/`get_parsed_token_accounts`: [5](#0-4) [6](#0-5) 

but the *mint field's target* is never subjected to the same "is this really a token-program account" check — the code implicitly trusts that any account referenced in the `mint` slot of a token-account's data blob is a legitimate mint, mirroring the reported bug where `_fromChain` is trusted without validating it against supported chains/origins.

## Impact Explanation
This is decoder/formatting logic that feeds `UiAccount`/`UiTokenAmount` fields returned by JSON-RPC (`getAccountInfo`, `getMultipleAccounts`, `getProgramAccounts`, `getTokenAccountsByOwner` with `jsonParsed` encoding, and `simulateTransaction` with `accounts` overrides via `overwrite_accounts`). If `StateWithExtensions::<Mint>::unpack` on an unrelated, non-mint account's data happens to parse "successfully" (or is manipulated via `simulateTransaction`'s account override feature, which lets an unprivileged caller substitute both a fake token account and/or a fake "mint" account with attacker-chosen owner and bytes), the RPC response can misreport `decimals`, `uiAmount`, and interest-bearing/scaled-UI-amount config for a token balance. This falls into the "decoder … misreporting" category of acceptable impact classes, since it can cause the validator's RPC layer to return incorrect token-amount metadata to a client without any consensus-affecting mutation.

## Likelihood Explanation
Reaching this path requires an unprivileged caller to either (a) find an on-chain account that happens to satisfy `Account::valid_account_data` with an attacker-influenced `mint` field pointing at a non-mint/foreign-owned account (constrained in practice by the SPL Token program's own `InitializeAccount` validation, which normally prevents such states from existing on-chain), or (b) use `simulateTransaction`'s `accounts` override capability to directly supply a token account whose embedded `mint` field references an arbitrary pubkey, and/or override that target account itself. Path (b) is directly reachable by any RPC caller with a single `simulateTransaction` call and needs no special privilege, but the actual security-relevant consequence is limited to display/formatting data in the simulation response rather than state or consensus. This significantly limits real-world exploitability/severity but the analog remains concretely reachable via unprivileged RPC parsing code with no chain-of-origin (owner) verification of the referenced mint account, distinguishing it from the original report's on-chain business-decision "risk accepted" framing.

## Recommendation
In `get_mint_owner_and_additional_data()` (and equivalently `get_additional_mint_data()`'s caller), verify that the fetched `mint_account.owner()` equals `spl_token_interface::id()` or `spl_token_2022_interface::id()` before attempting to unpack it as a `Mint`, mirroring the existing `is_known_spl_token_id()` check already performed on the token account itself. Reject/short-circuit additional-data computation (falling back to `None`) when the referenced mint account is not owned by a recognized token program, so JSON-parsed responses cannot report decimals/config derived from an unrelated account.

## Proof of Concept
Conceptual reproduction (not verified against a live cluster in this analysis):
1. Call `simulateTransaction` with `accounts.addresses` including a fabricated token-account-shaped account (owner = `TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA`, data crafted so bytes `[0..32)` reference an arbitrary target pubkey `X`, satisfying `Account::valid_account_data`).
2. Include `X` as another overridden or existing account with arbitrary `data`/`owner` (not the SPL Token program) that happens to be interpretable as packed `Mint` bytes (e.g., another SPL-Token-2022 mint reused from a different, unrelated context, or a hand-crafted 82+ byte blob).
3. Request `encoding: "jsonParsed"` for that account in the simulation's `accounts` config.
4. Observe that `get_parsed_token_account()` returns `UiTokenAmount`/`decimals` derived from `X`'s raw bytes despite `X` never being validated as a Token-program-owned mint, demonstrating that the "chain of origin" (owner) of the referenced mint is not checked before it is trusted for decoding. [7](#0-6)

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

**File:** rpc/src/parsed_token_accounts.rs (L52-88)
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

        let maybe_encoded_account = encode_ui_account(
            &pubkey,
            &account,
            UiAccountEncoding::JsonParsed,
            additional_data,
            None,
        );
        if let UiAccountData::Json(_) = &maybe_encoded_account.data {
            Some(RpcKeyedAccount {
                pubkey: pubkey.to_string(),
                account: maybe_encoded_account,
            })
        } else {
            None
        }
    })
}
```

**File:** rpc/src/parsed_token_accounts.rs (L92-130)
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

**File:** rpc/src/rpc.rs (L652-656)
```rust
        let accounts = if is_known_spl_token_id(&program_id)
            && encoding == UiAccountEncoding::JsonParsed
        {
            get_parsed_token_accounts(Arc::clone(&bank), keyed_accounts.into_iter()).collect()
        } else {
```

**File:** rpc/src/rpc.rs (L2552-2573)
```rust
fn get_encoded_account(
    bank: &Bank,
    pubkey: &Pubkey,
    encoding: UiAccountEncoding,
    data_slice: Option<UiDataSliceConfig>,
    // only used for simulation results
    overwrite_accounts: Option<&HashMap<Pubkey, AccountSharedData>>,
) -> Result<Option<UiAccount>> {
    match account_resolver::get_account_from_overwrites_or_bank(pubkey, bank, overwrite_accounts) {
        Some(account) => {
            let response = if is_known_spl_token_id(account.owner())
                && encoding == UiAccountEncoding::JsonParsed
            {
                get_parsed_token_account(bank, pubkey, account, overwrite_accounts)
            } else {
                encode_account(&account, pubkey, encoding, data_slice)?
            };
            Ok(Some(response))
        }
        None => Ok(None),
    }
}
```
