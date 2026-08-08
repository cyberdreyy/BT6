Based on the report's bug class—an external token's mutable/attacker-influenced properties silently breaking downstream logic that assumes well-behaved values—there is a concrete analog in Agave's SPL Token-2022 extension decoding used by unprivileged JSON-RPC handlers.

### Title
Attacker-controlled SPL Token-2022 `ScaledUiAmountConfig`/`InterestBearingConfig` values produce non-finite (`Infinity`/`NaN`) `ui_amount` fields returned by JSON-RPC token queries - (File: account-decoder/src/parse_token.rs)

### Summary
`token_amount_to_ui_amount_v3` computes a token's UI-facing amount using either the `InterestBearingConfig` or `ScaledUiAmountConfig` Token-2022 extensions when present on a mint. Both extensions store attacker-controlled numeric fields (`current_rate`/`pre_update_average_rate` for interest, `new_multiplier` as a raw `f64` for scaling) that anyone can set on a mint they control, since creating/configuring an SPL Token-2022 mint is fully permissionless. Extreme values cause the internal computation to overflow to `f64::INFINITY`/`NaN`, which is then embedded, unchecked, into the `UiTokenAmount` struct served over multiple unprivileged JSON-RPC endpoints.

### Finding Description [1](#0-0) 

`token_amount_to_ui_amount_v3` branches on whichever extension config is present and calls `interest_bearing_config.amount_to_ui_amount(...)` or `scaled_ui_amount_config.amount_to_ui_amount(...)`, converting the resulting string back into an `f64` with no finiteness validation: [2](#0-1) 

The repo's own tests demonstrate that crafted-but-in-spec extension data produces `Infinity`: [3](#0-2) [4](#0-3) 

For the interest-bearing case, `pre_update_average_rate`/`current_rate` at their maximum representable `i16` value (32767, i.e. ~327% APR) combined with an old `initialization_timestamp` is enough to overflow the compounding formula to `Infinity` — no invalid data is required, just extreme (but permitted) parameters. For `ScaledUiAmountConfig`, `new_multiplier` is a raw `f64` field with no bounds enforced by the extension itself, so a mint authority can literally set it to `f64::INFINITY` or `NaN` via the `UpdateMultiplier` instruction.

This function's output feeds directly into unprivileged, single-call RPC handlers that accept only a public key parameter:
- `get_token_account_balance` (`getTokenAccountBalance`)
- `get_token_supply` (`getTokenSupply`)
- `get_token_largest_accounts` (`getTokenLargestAccounts`) [5](#0-4) 

as well as `jsonParsed`-encoded account fetches (`getAccountInfo`, `getProgramAccounts`, `getTokenAccountsByOwner`) via `get_parsed_token_account`/`get_parsed_token_accounts`, which resolve the mint's extension data through `get_additional_mint_data`: [6](#0-5) [7](#0-6) 

Since these RPC methods only require a pubkey the caller supplies (which can point at any mint or token account created by any user), an attacker can create a Token-2022 mint with an extreme `ScaledUiAmountConfig`/`InterestBearingConfig`, then trigger a single unprivileged RPC call against it. Downstream, the non-finite `f64` is serialized into the JSON-RPC response as part of `UiTokenAmount.ui_amount`. `serde_json`'s float formatter (`ryu::Buffer::format_finite`) is documented to only accept finite values; passing `Infinity`/`NaN` is not validated for at any point in this code path, meaning any RPC response embedding this value is either malformed/misreported or, depending on build configuration, can hit `ryu`'s internal finiteness assertion.

### Impact Explanation
A single unprivileged JSON-RPC call to a permissionless, attacker-authored mint account produces corrupted account/token-amount data (`ui_amount`/`ui_amount_string` reporting `Infinity`/`inf`/`NaN`) in the response of core RPC endpoints (`getTokenSupply`, `getTokenAccountBalance`, `getAccountInfo`, `getProgramAccounts`, `getTokenAccountsByOwner`). This is a decoder misreporting bug reachable purely by any unprivileged caller pointing the RPC at a mint they created, and it can additionally reach `debug_assert!`-guarded UB paths in the underlying float formatting crate.

### Likelihood Explanation
High. Creating an SPL Token-2022 mint and setting an extreme `ScaledUiAmountConfig.new_multiplier` (an unconstrained `f64` field) or a maximal `InterestBearingConfig` rate is entirely permissionless and requires no special privilege — only a single mint-creation and a single extension-update transaction, followed by any ordinary RPC query against that mint/account.

### Recommendation
Validate finiteness of the computed `ui_amount` (and of `new_multiplier`/rate-derived values) in `token_amount_to_ui_amount_v3` before constructing `UiTokenAmount`, falling back to `None`/an explicit sentinel string rather than propagating `Infinity`/`NaN` into RPC responses.

### Proof of Concept [4](#0-3) 
1. Create an SPL Token-2022 mint with the `ScaledUiAmountConfig` extension.
2. Call `UpdateMultiplier` setting `new_multiplier` to the bit pattern of `f64::INFINITY` (or `NaN`).
3. Call `getTokenSupply` (or `getAccountInfo` with `jsonParsed` encoding) on that mint via JSON-RPC.
4. Observe `token_amount_to_ui_amount_v3` (as reproduced by the repo's own unit test) returns `ui_amount: Some(f64::INFINITY)` / `ui_amount_string: "inf"`, which is embedded in the RPC response without validation.

### Citations

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

**File:** account-decoder/src/parse_token.rs (L402-417)
```rust
        // huge case
        let config = InterestBearingConfig {
            initialization_timestamp: 0.into(),
            pre_update_average_rate: 32767.into(),
            last_update_timestamp: 0.into(),
            current_rate: 32767.into(),
            ..Default::default()
        };
        let additional_data = SplTokenAdditionalDataV2 {
            decimals: 0,
            interest_bearing_config: Some((config, INT_SECONDS_PER_YEAR * 1_000)),
            ..Default::default()
        };
        let token_amount = token_amount_to_ui_amount_v3(u64::MAX, &additional_data);
        assert_eq!(token_amount.ui_amount, Some(f64::INFINITY));
        assert_eq!(token_amount.ui_amount_string, "inf");
```

**File:** account-decoder/src/parse_token.rs (L442-454)
```rust
        // huge case
        let config = ScaledUiAmountConfig {
            new_multiplier: f64::INFINITY.into(),
            ..Default::default()
        };
        let additional_data = SplTokenAdditionalDataV2 {
            decimals: 0,
            scaled_ui_amount_config: Some((config, 0)),
            ..Default::default()
        };
        let token_amount = token_amount_to_ui_amount_v3(u64::MAX, &additional_data);
        assert_eq!(token_amount.ui_amount, Some(f64::INFINITY));
        assert_eq!(token_amount.ui_amount_string, "inf");
```

**File:** rpc/src/rpc.rs (L2013-2074)
```rust
    pub fn get_token_account_balance(
        &self,
        pubkey: &Pubkey,
        commitment: Option<CommitmentConfig>,
    ) -> Result<RpcResponse<UiTokenAmount>> {
        let bank = self.bank(commitment);
        let account = bank.get_account(pubkey).ok_or_else(|| {
            Error::invalid_params("Invalid param: could not find account".to_string())
        })?;

        if !is_known_spl_token_id(account.owner()) {
            return Err(Error::invalid_params(
                "Invalid param: not a Token account".to_string(),
            ));
        }
        let token_account = StateWithExtensions::<TokenAccount>::unpack(account.data())
            .map_err(|_| Error::invalid_params("Invalid param: not a Token account".to_string()))?;
        let mint = &Pubkey::from_str(&token_account.base.mint.to_string())
            .expect("Token account mint should be convertible to Pubkey");
        let (_, data) = get_mint_owner_and_additional_data(&bank, mint)?;
        let balance = token_amount_to_ui_amount_v3(token_account.base.amount, &data);
        Ok(new_response(&bank, balance))
    }

    pub fn get_token_supply(
        &self,
        mint: &Pubkey,
        commitment: Option<CommitmentConfig>,
    ) -> Result<RpcResponse<UiTokenAmount>> {
        let bank = self.bank(commitment);
        let mint_account = bank.get_account(mint).ok_or_else(|| {
            Error::invalid_params("Invalid param: could not find account".to_string())
        })?;
        if !is_known_spl_token_id(mint_account.owner()) {
            return Err(Error::invalid_params(
                "Invalid param: not a Token mint".to_string(),
            ));
        }
        let mint = StateWithExtensions::<Mint>::unpack(mint_account.data()).map_err(|_| {
            Error::invalid_params("Invalid param: mint could not be unpacked".to_string())
        })?;

        let interest_bearing_config = mint
            .get_extension::<InterestBearingConfig>()
            .map(|x| (*x, bank.clock().unix_timestamp))
            .ok();

        let scaled_ui_amount_config = mint
            .get_extension::<ScaledUiAmountConfig>()
            .map(|x| (*x, bank.clock().unix_timestamp))
            .ok();

        let supply = token_amount_to_ui_amount_v3(
            mint.base.supply,
            &SplTokenAdditionalDataV2 {
                decimals: mint.base.decimals,
                interest_bearing_config,
                scaled_ui_amount_config,
            },
        );
        Ok(new_response(&bank, supply))
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
