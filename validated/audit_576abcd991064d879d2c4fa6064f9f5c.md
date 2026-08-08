### Title
Silent priority collision between InterestBearingConfig and ScaledUiAmountConfig in `token_amount_to_ui_amount_v3` produces semantically wrong (but well-formed) `ui_amount_string`/`ui_amount` when both extensions coexist on a mint - ([File: account-decoder/src/parse_token.rs])

### Summary
`get_additional_mint_data` in `rpc/src/parsed_token_accounts.rs` unconditionally extracts both `InterestBearingConfig` and `ScaledUiAmountConfig` from a mint's TLV extensions with no mutual-exclusivity check, storing both into `SplTokenAdditionalDataV2`. `token_amount_to_ui_amount_v3` then applies an `if/else-if` chain that always prioritizes `interest_bearing_config` over `scaled_ui_amount_config`, so if both are present, the scaled-multiplier calculation is silently discarded rather than combined or rejected.

### Finding Description
`get_additional_mint_data` unpacks the mint and independently calls `get_extension::<InterestBearingConfig>()` and `get_extension::<ScaledUiAmountConfig>()`, populating both fields of `SplTokenAdditionalDataV2` if the corresponding TLVs are present in the raw account bytes: [1](#0-0) 

`token_amount_to_ui_amount_v3` then branches with `if let Some(interest_bearing_config) = ... else if let Some(scaled_ui_amount_config) = ...`, meaning when both are `Some`, only the interest-bearing calculation runs and the scaled-ui-amount multiplier is completely ignored: [2](#0-1) 

Because the extension unpacking (`mint.get_extension::<T>()`) reads TLV data structurally without cross-validating exclusivity between extension types, raw account bytes that place both TLVs in the mint's extension region are unpacked into two `Some` values here, even if `spl-token-2022`'s initialization instructions would normally reject creating a mint with both extensions.

### Impact Explanation
This is a data-integrity/misreporting issue reachable via a single unprivileged read RPC call (`getAccountInfo` with `jsonParsed` encoding, or `getTokenAccountBalance`) against a token account whose mint contains attacker-crafted bytes with both extension TLVs populated. All clients querying accounts under that mint receive a `ui_amount`/`ui_amount_string` that reflects only the interest-bearing calculation, silently dropping the scaled-ui-amount multiplier — a semantically corrupted but syntactically valid balance report, matching the "wrong-slot/fork/account data returned" category in scope.

### Likelihood Explanation
Feasibility depends entirely on whether raw mint bytes with both extension TLVs simultaneously present can actually exist on-chain. This requires the attacker to get such bytes committed to a mint account (e.g., via a custom/malicious on-chain program that writes token-2022-formatted TLV data without going through the real `spl-token-2022` extension-initialization guardrails, since that program enforces mutual exclusivity at instruction-processing time). The Agave RPC/decoder code performs no independent validation of TLV internal consistency; it trusts and unpacks whatever extensions are structurally present. I could not verify from this repo alone (no access to the `spl-token-2022` extension TLV layout/`get_extension` implementation, which lives in the external `spl_token_2022_interface` crate) whether `get_extension::<T>()` would actually return `Some` for both types simultaneously, or whether the TLV format itself prevents two extensions from coexisting at arbitrary offsets, or whether writing such bytes to an account requires going through the token program (which enforces exclusivity) versus an arbitrary on-chain program (which does not, since Agave does not restrict what bytes any owned program writes to accounts it owns). This crate-boundary uncertainty is the single most important unresolved question for exploitability.

### Recommendation
In `get_additional_mint_data` (`rpc/src/parsed_token_accounts.rs`), and/or in `token_amount_to_ui_amount_v3` (`account-decoder/src/parse_token.rs`), explicitly detect when both `interest_bearing_config` and `scaled_ui_amount_config` are present and either: (a) apply the intended combination logic if the two extensions are meant to compose, or (b) surface a decode-time error/warning (e.g., an explicit "conflicting mint extensions" error) instead of silently dropping one, so callers relying on parse fidelity are not misled.

### Proof of Concept
Rust unit test to add to `account-decoder/src/parse_token.rs` test module, constructing a `SplTokenAdditionalDataV2` with both `interest_bearing_config` and `scaled_ui_amount_config` populated and asserting that `token_amount_to_ui_amount_v3`'s output ignores the multiplier entirely (demonstrating the silent drop):

```rust
#[test]
fn test_ui_token_amount_conflicting_extensions_silently_drops_scaling() {
    let interest_config = InterestBearingConfig {
        current_rate: 500.into(),
        ..Default::default()
    };
    let scaled_config = ScaledUiAmountConfig {
        new_multiplier: 2f64.into(),
        ..Default::default()
    };
    let additional_data = SplTokenAdditionalDataV2 {
        decimals: 9,
        interest_bearing_config: Some((interest_config, 0)),
        scaled_ui_amount_config: Some((scaled_config, 0)),
    };
    let amount = 1_000_000_000u64;
    let result = token_amount_to_ui_amount_v3(amount, &additional_data);
    // Expected (if properly combined): interest-adjusted amount * 2x multiplier.
    // Actual: only interest-bearing branch executes; scaled_ui_amount_config
    // is never applied, producing a mismatched/incomplete result relative to
    // ground truth if both extensions were genuinely intended to compose.
    assert!(!result.ui_amount_string.is_empty());
    // Demonstrate multiplier was NOT applied by comparing against interest-only computation:
    let interest_only_data = SplTokenAdditionalDataV2 {
        decimals: 9,
        interest_bearing_config: Some((interest_config, 0)),
        scaled_ui_amount_config: None,
    };
    let interest_only_result = token_amount_to_ui_amount_v3(amount, &interest_only_data);
    assert_eq!(result.ui_amount_string, interest_only_result.ui_amount_string);
}
```

This confirms the branch-priority behavior at the Agave decoder level. Full end-to-end exploitability (whether raw bytes with both TLVs coexisting can be committed on-chain and read back by `get_additional_mint_data`) requires further investigation against the `spl-token-2022` TLV extension format outside this repo's index.

### Citations

**File:** rpc/src/parsed_token_accounts.rs (L110-129)
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
```

**File:** account-decoder/src/parse_token.rs (L125-157)
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
```
