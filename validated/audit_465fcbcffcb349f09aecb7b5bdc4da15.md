### Title
JSON-parsed token-account decoding derives mint/decimals from an unvalidated, attacker-controlled `mint` field, causing misreported balances - (File: `rpc/src/parsed_token_accounts.rs`)

### Summary
The external report's bug class is: two user-supplied fields that are supposed to correspond to each other (underlying asset vs. AMM pool) are never checked to actually match, letting an attacker substitute an unrelated asset/pool pair and steal value. The agave analog is in the RPC `jsonParsed` token-account decoding path, where the "mint" field embedded in a token account's raw data and the actual `mint` account fetched from the bank are never checked to correspond to the same token program, allowing an attacker to make the decoder pull decimals/extension data from an arbitrary, attacker-chosen account and misreport a token account's UI balance.

### Finding Description
`get_parsed_token_account` and `get_parsed_token_accounts` in [1](#0-0)  extract the mint pubkey directly from the raw bytes of a token account (`get_token_account_mint`, which only checks `Account::valid_account_data` — a magic-byte-style check on the *token account*, not the mint) and then fetch whatever account lives at that pubkey via `get_account_from_overwrites_or_bank` / `bank.get_account(mint)`.

`get_mint_owner_and_additional_data` in the same file [2](#0-1)  and `get_additional_mint_data` [3](#0-2)  never verify that the fetched "mint" account is owned by the same SPL Token / Token-2022 program as the token account being decoded (or even that it is a legitimate mint at all) — they simply attempt `StateWithExtensions::<Mint>::unpack(data)` on whatever bytes exist at that address and use the resulting `decimals`/extension config (e.g. `InterestBearingConfig`, `ScaledUiAmountConfig`) to compute the UI amount via `token_amount_to_ui_amount_v3` in [4](#0-3) .

This is exactly the missing-correspondence-check pattern from the report: the "mint" reference and the actual mint data are user-influenced (an attacker fully controls both the token account they create and the address they point its `mint` field at) but are never validated to belong together. Notably, the SVM's own transaction-balance code performs the correct check and rejects mismatched owners — `svm/src/transaction_balances.rs` explicitly checks `if *mint_account.owner() != program_id { return None; }` [5](#0-4)  before trusting mint data — but this owner check is absent from the RPC `jsonParsed` decoding path used by `getTokenAccountsByOwner`, `getTokenAccountsByDelegate`, `getProgramAccounts` (jsonParsed) at [6](#0-5) [7](#0-6) , and `getAccountInfo`/pubsub account subscriptions that call `get_parsed_token_account`.

### Impact Explanation
An attacker can create (or already control) a token-account-shaped account whose embedded `mint` field points at an arbitrary account (e.g., one they also control, or an unrelated existing account whose bytes happen to unpack as a `Mint`) with wildly different `decimals` or interest-bearing/scaled-UI-amount extension parameters. When any client queries via `getTokenAccountsByOwner`, `getTokenAccountsByDelegate`, `getProgramAccounts` (jsonParsed), `getAccountInfo`, or account subscriptions, the RPC node will report a fabricated `uiAmount`/`uiAmountString` for that token account. This is a decoder-misreporting issue (explicitly in scope per the validation criteria) — wallets, explorers, and downstream integrations that trust `jsonParsed` RPC output could display a manipulated balance for an account, which could be leveraged in social-engineering / fake-deposit style attacks (e.g., making a worthless token account appear to hold a large "legitimate" balance).

### Likelihood Explanation
Likelihood is high for triggering the misreporting itself: any unprivileged user can craft such an account with a single transaction (creating a token account and pointing its mint field anywhere) and then query it via ordinary, single-call JSON-RPC methods — no special privileges, races, or multi-client coordination are required. This differs from the original DeFi report in that there is no consensus-state or lamport-value theft on the validator side; the impact is confined to RPC response integrity (misreporting), which is the accepted analog category here.

### Recommendation
Before using a "mint" account's data to compute `SplTokenAdditionalDataV2`/UI amounts, validate that the mint account's owner equals the same token program that owns the token account being decoded (mirroring the check already present in `svm/src/transaction_balances.rs::unpack_token_account`). Reject or fall back to raw encoding when this invariant does not hold, in both `get_parsed_token_account` and `get_parsed_token_accounts` in `rpc/src/parsed_token_accounts.rs`.

### Proof of Concept
1. Attacker submits a transaction creating a normal SPL Token (or Token-2022) account `A` owned by the token program, with `A.mint` field set to the address of account `B`.
2. Attacker separately creates account `B` (owned by any program, or even the same token program) whose raw bytes happen to unpack successfully as a `Mint` (only requires matching the `Mint`/`StateWithExtensions<Mint>` byte layout) with `decimals` set to an attacker-chosen extreme value (e.g., 0) and/or a `ScaledUiAmountConfig`/`InterestBearingConfig` extension with an inflated scale factor.
3. Attacker calls `getTokenAccountsByOwner`/`getAccountInfo` with `encoding: jsonParsed` for account `A`.
4. `get_parsed_token_account`/`get_parsed_token_accounts` [1](#0-0)  extracts `mint = B` from `A`'s raw data, fetches `B` from the bank with no owner check, unpacks it as a `Mint`, and feeds its `decimals`/extension config into `token_amount_to_ui_amount_v3` [4](#0-3) , producing a `uiAmount`/`uiAmountString` for `A` that has no relationship to any legitimate mint for that token, demonstrating the misreporting.

### Citations

**File:** rpc/src/parsed_token_accounts.rs (L23-88)
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

**File:** account-decoder/src/parse_token.rs (L125-164)
```rust
pub fn token_amount_to_ui_amount_v3(
    amount: u64,
    additional_data: &SplTokenAdditionalDataV2,
) -> UiTokenAmount {
    let decimals = additional_data.decimals;
    let (ui_amount, ui_amount_string) = if let Some((interest_bearing_config, unix_timestamp)) =
        additional_data.interest_bearing_config
    {
        let ui_amount_string =
            interest_bearing_config.amount_to_ui_amount(amount, decimals, unix_timestamp);
        (
            ui_amount_string
                .as_ref()
                .and_then(|x| f64::from_str(x).ok()),
            ui_amount_string.unwrap_or("".to_string()),
        )
    } else if let Some((scaled_ui_amount_config, unix_timestamp)) =
        additional_data.scaled_ui_amount_config
    {
        let ui_amount_string =
            scaled_ui_amount_config.amount_to_ui_amount(amount, decimals, unix_timestamp);
        (
            ui_amount_string
                .as_ref()
                .and_then(|x| f64::from_str(x).ok()),
            ui_amount_string.unwrap_or("".to_string()),
        )
    } else {
        let ui_amount = 10_usize
            .checked_pow(decimals as u32)
            .map(|dividend| amount as f64 / dividend as f64);
        (ui_amount, real_number_string_trimmed(amount, decimals))
    };
    UiTokenAmount {
        ui_amount,
        decimals,
        amount: amount.to_string(),
        ui_amount_string,
    }
}
```

**File:** svm/src/transaction_balances.rs (L186-190)
```rust

        let mint_account = account_loader.load_account(&mint)?;
        if *mint_account.owner() != program_id {
            return None;
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

**File:** rpc/src/rpc.rs (L2170-2171)
```rust
        let accounts = if encoding == UiAccountEncoding::JsonParsed {
            get_parsed_token_accounts(bank.clone(), keyed_accounts.into_iter()).collect()
```
