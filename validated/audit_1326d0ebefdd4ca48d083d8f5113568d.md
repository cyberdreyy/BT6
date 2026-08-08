Based on my research, this maps reasonably well to a real analog: `token_amount_to_ui_amount_v3` in `account-decoder/src/parse_token.rs` silently accepts an out-of-range/overflowed result (`f64::INFINITY`) as a valid `ui_amount` for interest-bearing and scaled-UI-amount SPL Token-2022 mints, instead of detecting the overflow condition and returning `None`/an error the way the plain-decimals path does via `checked_pow`.

### Title
SPL Token-2022 interest-bearing/scaled UI amount decoding silently returns `Infinity` instead of detecting overflow, misreporting token balances - (File: account-decoder/src/parse_token.rs)

### Summary
`token_amount_to_ui_amount_v3` computes `ui_amount` differently depending on whether a mint has an `InterestBearingConfig` or `ScaledUiAmountConfig` extension. For plain SPL tokens, the divisor is computed with `10_usize.checked_pow(decimals as u32)`, and if it overflows the function correctly returns `None` for `ui_amount` [1](#0-0) . For interest-bearing or scaled-UI-amount mints, however, the UI amount is derived from `interest_bearing_config.amount_to_ui_amount(...)` / `scaled_ui_amount_config.amount_to_ui_amount(...)` and parsed back with `f64::from_str`, with no bounds/overflow check at all [2](#0-1) . When the underlying calculation overflows `f64`, the result is silently `Infinity`/`"inf"` rather than an error or `None`.

### Finding Description
The extension-aware branch of `token_amount_to_ui_amount_v3` trusts whatever string `amount_to_ui_amount` produces and blindly parses it into `f64`, without any range/finite check comparable to the `checked_pow` guard used in the non-extension branch. This is directly demonstrated by the repo's own unit test, which shows that with attacker/mint-authority-controlled fields (`current_rate`, `pre_update_average_rate`, or `new_multiplier`) and a large raw token `amount` (e.g. `u64::MAX`), the resulting `ui_amount` becomes `Some(f64::INFINITY)` and `ui_amount_string` becomes `"inf"` [3](#0-2) , and similarly for the scaled UI amount config with an `Infinity` multiplier [4](#0-3) . This is the same bug class as the Chainlink report: a boundary/overflow condition in an upstream numeric computation is not checked before the value is trusted and returned as "the" answer, so wildly wrong data (`Infinity`) is presented as if it were a normal, valid balance.

This code path is reachable by any unprivileged RPC caller. It is used by:
- `getTokenAccountBalance` / `getTokenSupply` via `token_amount_to_ui_amount_v3` in `rpc/src/rpc.rs` [5](#0-4) [6](#0-5) 
- `getAccountInfo`/`getProgramAccounts` with `jsonParsed` encoding for SPL Token-2022 accounts, via `parse_token_v3` [7](#0-6) 
- Mint decimals/extension data is fetched from the mint account itself in `get_additional_mint_data`, so a mint authority (or any account holding a mint with attacker-influenced extension fields) fully controls the config values fed into this calculation [8](#0-7) .

### Impact Explanation
This causes an RPC node to return `"ui_amount": Infinity` / `"ui_amount_string": "inf"` for token balances/supply to any unprivileged client querying `getTokenAccountBalance`, `getTokenSupply`, or `getAccountInfo`/`getProgramAccounts` with `jsonParsed` encoding on a Token-2022 mint with interest-bearing or scaled-UI-amount extensions and a large stored amount. Any downstream consumer (wallets, exchanges, indexers) that parses this JSON as a finite number will get wrong/undefined behavior, and `serde_json` serialization of `f64::INFINITY` is non-standard JSON, which can itself break strict JSON parsers on the client side — effectively "decoder misreporting" of account data to any caller of these public JSON-RPC methods, matching the accepted impact category for account decoding/RPC misreporting.

### Likelihood Explanation
Likelihood is low-to-moderate: it requires a Token-2022 mint with an interest-bearing or scaled-UI-amount extension configured with an extreme rate/multiplier and a token amount large enough to overflow the `f64` computation. This is achievable by any mint creator (unprivileged from the validator's perspective) without needing validator/operator privileges, so it is a legitimate unprivileged-user-triggerable RPC misreporting bug, though it is a more benign/UX-facing bug than a crash or consensus issue.

### Recommendation
In `token_amount_to_ui_amount_v3`, after computing `ui_amount` from `interest_bearing_config.amount_to_ui_amount` or `scaled_ui_amount_config.amount_to_ui_amount`, validate the parsed `f64` with `.filter(|v| v.is_finite())` (mirroring the `checked_pow`-based `None` fallback in the plain-decimals branch) so that overflow/non-finite results are reported as `None`/an explicit error rather than silently returned as `Infinity`.

### Proof of Concept
The existing test `test_ui_token_amount_with_interest` in `account-decoder/src/parse_token.rs` already demonstrates the exact condition: constructing an `InterestBearingConfig` with `current_rate`/`pre_update_average_rate` set to `32767` (max `i16`) and a long elapsed duration, then calling `token_amount_to_ui_amount_v3(u64::MAX, &additional_data)` yields `ui_amount = Some(f64::INFINITY)` and `ui_amount_string = "inf"` [3](#0-2) . The same happens for `ScaledUiAmountConfig` with `new_multiplier: f64::INFINITY` [4](#0-3) . Reaching this via RPC requires creating such a Token-2022 mint on-chain and then calling `getTokenSupply`/`getTokenAccountBalance`/`getAccountInfo` (`jsonParsed`) for that mint/account.

### Citations

**File:** account-decoder/src/parse_token.rs (L39-68)
```rust
        return Ok(TokenAccountType::Account(UiTokenAccount {
            mint: account.base.mint.to_string(),
            owner: account.base.owner.to_string(),
            token_amount: token_amount_to_ui_amount_v3(account.base.amount, additional_data),
            delegate: match account.base.delegate {
                COption::Some(pubkey) => Some(pubkey.to_string()),
                COption::None => None,
            },
            state: convert_account_state(account.base.state),
            is_native: account.base.is_native(),
            rent_exempt_reserve: match account.base.is_native {
                COption::Some(reserve) => {
                    Some(token_amount_to_ui_amount_v3(reserve, additional_data))
                }
                COption::None => None,
            },
            delegated_amount: if account.base.delegate.is_none() {
                None
            } else {
                Some(token_amount_to_ui_amount_v3(
                    account.base.delegated_amount,
                    additional_data,
                ))
            },
            close_authority: match account.base.close_authority {
                COption::Some(pubkey) => Some(pubkey.to_string()),
                COption::None => None,
            },
            extensions: ui_extensions,
        }));
```

**File:** account-decoder/src/parse_token.rs (L130-151)
```rust
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
```

**File:** account-decoder/src/parse_token.rs (L152-157)
```rust
    } else {
        let ui_amount = 10_usize
            .checked_pow(decimals as u32)
            .map(|dividend| amount as f64 / dividend as f64);
        (ui_amount, real_number_string_trimmed(amount, decimals))
    };
```

**File:** account-decoder/src/parse_token.rs (L402-418)
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
    }
```

**File:** account-decoder/src/parse_token.rs (L442-455)
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
    }
```

**File:** rpc/src/rpc.rs (L2013-2035)
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
```

**File:** rpc/src/rpc.rs (L2037-2074)
```rust
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
