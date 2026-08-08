### Title
`getTokenAccountBalance`/`getTokenSupply`/`getTokenLargestAccounts` misreport balances as `null` when a Token‑2022 mint uses extreme `ScaledUiAmountConfig`/`InterestBearingConfig` values, causing `f64` overflow to infinity - (File: account-decoder/src/parse_token.rs)

### Summary
`token_amount_to_ui_amount_v3` in `account-decoder/src/parse_token.rs` computes the human-readable `ui_amount` for SPL Token / Token-2022 balances using `f64` arithmetic driven by mint-extension parameters (`ScaledUiAmountConfig.new_multiplier`, `InterestBearingConfig.current_rate`/`pre_update_average_rate`) that are fully attacker-controlled since anyone can create a Token-2022 mint with arbitrary extension config values. This mirrors the reported bug class: an `f64`-based price/amount calculation whose result can exceed representable range and gets silently clamped/converted to an unusable value, producing incorrect data to callers.

### Finding Description
`token_amount_to_ui_amount_v3` dispatches to extension-specific amount conversions when a mint has `InterestBearingConfig` or `ScaledUiAmountConfig`: [1](#0-0) 

Both extension paths perform floating point math with attacker-supplied fields (interest rate, elapsed time, multiplier) that is not bounded before being used. The existing unit tests in the same file demonstrate that these calculations reach `f64::INFINITY` for extreme (but validly encodable) extension parameters: [2](#0-1) [3](#0-2) 

The `UiTokenAmount.ui_amount` field is a plain `Option<f64>` that is serialized directly into the JSON-RPC response: [4](#0-3) 

These conversions are reachable directly from unprivileged JSON-RPC handlers such as `get_token_account_balance`, `get_token_supply`, and `get_token_largest_accounts`, which read the mint's extension configuration and pass it straight into `token_amount_to_ui_amount_v3`: [5](#0-4) [6](#0-5) 

Because `serde_json`'s float serializer emits `null` for non-finite `f64` values (NaN/Infinity are not valid JSON numbers), the resulting RPC response silently reports `"uiAmount": null` (or an "inf" `ui_amount_string`) for a token balance/supply that is actually non-zero and well-defined in raw `amount` terms — i.e., wrong/misleading account data is returned to any caller of these read-only endpoints, exactly analogous to the reported `get_reserve` bug where an out-of-range `f64`-derived value silently collapses to a degenerate result (zero/clamped) instead of the correct price.

### Impact Explanation
This is a pure information-correctness bug reachable via a single unprivileged RPC call against any Token-2022 mint the attacker controls (mint creation and extension configuration is fully permissionless and requires no validator/operator privilege). Any consumer of `getTokenAccountBalance`, `getTokenSupply`, or `getTokenLargestAccounts` for such a mint receives an incorrect/degenerate `ui_amount` (`null`) despite a real underlying balance, which can mislead wallets, explorers, and downstream automated systems that trust the RPC-reported UI amount, potentially leading to trading/settlement decisions based on wrong balance information. There is no validator crash or consensus impact; the raw `amount` string field itself remains correct, limiting the severity to a display/misreporting issue in the parsed/decoded view exposed by JSON-RPC.

### Likelihood Explanation
Trivial to trigger: an attacker only needs to create a standard Token-2022 mint and initialize it with a `ScaledUiAmountConfig` multiplier or `InterestBearingConfig` rate that, combined with the elapsed time since initialization, drives the computed `f64` value to overflow. No special access, timing, or validator cooperation is required, and the affected RPC methods are public read endpoints most RPC nodes expose.

### Recommendation
Bound the computed `ui_amount`/`ui_amount_string` explicitly: detect non-finite results from `amount_to_ui_amount` (interest-bearing/scaled configs) before constructing `UiTokenAmount`, and either clamp to a defined maximum/minimum, return an explicit error, or preserve precision via a decimal/string-based representation rather than raw `f64`, so JSON-RPC responses never silently degrade to `null`/`"inf"` for legitimate token balances.

### Proof of Concept
Reachable directly from the existing test suite (no new tooling required to demonstrate the root cause), showing `token_amount_to_ui_amount_v3` returning `Some(f64::INFINITY)`/`"inf"` for both extension paths: [2](#0-1) [3](#0-2) 
End-to-end: create a Token-2022 mint with `ScaledUiAmountConfig{ new_multiplier: f64::INFINITY-inducing value }` (or an `InterestBearingConfig` with a high rate over a long elapsed period), mint tokens to an account, then call `getTokenAccountBalance`/`getTokenSupply` against that account/mint via the RPC handlers at [5](#0-4)  and observe `"uiAmount": null` in the JSON-RPC response despite a nonzero raw `amount`.

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

**File:** account-decoder-client-types/src/token.rs (L1-13)
```rust
use {
    core::str::FromStr,
    serde::{Deserialize, Serialize},
};

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct UiTokenAmount {
    pub ui_amount: Option<f64>,
    pub decimals: u8,
    pub amount: String,
    pub ui_amount_string: String,
}
```

**File:** rpc/src/rpc.rs (L2013-2073)
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
```

**File:** rpc/src/rpc.rs (L2076-2130)
```rust
    pub async fn get_token_largest_accounts(
        &self,
        mint: Pubkey,
        commitment: Option<CommitmentConfig>,
    ) -> Result<RpcResponse<Vec<RpcTokenAccountBalance>>> {
        let bank = self.bank(commitment);
        let (mint_owner, data) = get_mint_owner_and_additional_data(&bank, &mint)?;
        if !is_known_spl_token_id(&mint_owner) {
            return Err(Error::invalid_params(
                "Invalid param: not a Token mint".to_string(),
            ));
        }

        let mut token_balances =
            BinaryHeap::<Reverse<(u64, Pubkey)>>::with_capacity(NUM_LARGEST_ACCOUNTS);
        for (address, account) in self
            .get_filtered_spl_token_accounts_by_mint(
                Arc::clone(&bank),
                mint_owner,
                mint,
                vec![],
                true,
            )
            .await?
        {
            let amount = StateWithExtensions::<TokenAccount>::unpack(account.data())
                .map(|account| account.base.amount)
                .unwrap_or(0);

            let new_entry = (amount, address);
            if token_balances.len() >= NUM_LARGEST_ACCOUNTS {
                let Reverse(entry) = token_balances
                    .peek()
                    .expect("BinaryHeap::peek should succeed when len > 0");
                if *entry >= new_entry {
                    continue;
                }
                token_balances.pop();
            }
            token_balances.push(Reverse(new_entry));
        }

        let token_balances = token_balances
            .into_sorted_vec()
            .into_iter()
            .map(|Reverse((amount, address))| {
                Ok(RpcTokenAccountBalance {
                    address: address.to_string(),
                    amount: token_amount_to_ui_amount_v3(amount, &data),
                })
            })
            .collect::<Result<Vec<_>>>()?;

        Ok(new_response(&bank, token_balances))
    }
```
