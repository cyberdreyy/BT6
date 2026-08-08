### Title
`StoredTokenAmount::from` recomputes `ui_amount_string` without interest/scaling, producing internally inconsistent `UiTokenAmount` for interest-bearing/scaled-UI-amount tokens - ([File: storage-proto/src/lib.rs])

### Summary
`impl From<StoredTokenAmount> for UiTokenAmount` in `storage-proto/src/lib.rs` trusts the persisted `ui_amount: f64` verbatim while recomputing `ui_amount_string` from raw `amount`/`decimals` via `real_number_string_trimmed`, with no knowledge of interest-bearing or scaled-UI-amount extension configuration. For SPL Token-2022 mints using these extensions, the originally stored `ui_amount` reflects an interest/scale-adjusted value computed at persist time (via `token_amount_to_ui_amount_v3` in `account-decoder/src/parse_token.rs`), while the recomputed `ui_amount_string` reflects only the raw base amount, yielding a `UiTokenAmount` whose two "ui" fields disagree for the same on-chain event.

### Finding Description
The write path converts a `UiTokenAmount` (computed at transaction time, potentially via `token_amount_to_ui_amount_v3`, which for interest-bearing or scaled-UI-amount mints applies `interest_bearing_config.amount_to_ui_amount(...)` or `scaled_ui_amount_config.amount_to_ui_amount(...)`) into `StoredTokenAmount` [1](#0-0) , discarding the original `ui_amount_string` and keeping only `ui_amount`, `decimals`, and raw `amount`.

On read-back (e.g., via bigtable-backed `getTransaction`/`getConfirmedTransaction`), `impl From<StoredTokenAmount> for UiTokenAmount` reconstructs the record by taking the stored `ui_amount` as-is, but recomputes `ui_amount_string` purely from `amount`/`decimals` using `real_number_string_trimmed`, with no interest or scaling applied: [2](#0-1) 

Compare this with the authoritative combined computation in `token_amount_to_ui_amount_v3`, which computes both `ui_amount` and `ui_amount_string` together from the same interest/scaling config so they stay consistent: [3](#0-2) . The stored/restored path in `storage-proto` has no such config and cannot recompute `ui_amount_string` consistently with the already-stored `ui_amount`.

Any unprivileged user can: (1) create an interest-bearing (or scaled-UI-amount) SPL Token-2022 mint, (2) execute a transfer, causing the runtime to record a `TransactionTokenBalance`/`UiTokenAmount` with an interest-adjusted `ui_amount` (e.g., `1.0512710963760241` per the extension test at `account-decoder/src/parse_token.rs:371-400`), (3) have that record persisted to bigtable/blockstore via `TryFrom<TransactionStatusMeta>`/`From<UiTokenAmount> for StoredTokenAmount`, and (4) later query the same historical transaction via `getConfirmedTransaction`/`getTransaction`, triggering `From<StoredTokenAmount> for UiTokenAmount`. The response will contain `ui_amount = 1.0512710963760241` alongside `ui_amount_string` computed only from raw `amount`/`decimals` (i.e., the un-adjusted base value, e.g. `"1"`), which is not derivable from — and contradicts — the stored `ui_amount`.

### Impact Explanation
This is a PARSE_FIDELITY / wrong-data-returned issue: `getConfirmedTransaction`/`getTransaction` responses for historical transactions involving interest-bearing or scaled-UI-amount SPL Token-2022 balances return a `UiTokenAmount` whose `ui_amount` and `ui_amount_string` fields describe two different quantities for the same event. Downstream consumers (explorers, wallets, accounting tools) that trust either field independently will misreport the historical token amount for that transaction. Impact is scoped to misreported/inconsistent historical RPC data, not consensus, crash, or privileged access.

### Likelihood Explanation
This requires no attacker privilege beyond creating a standard SPL Token-2022 mint with the interest-bearing or scaled-UI-amount extension and performing an ordinary transfer — both are permissionless, one-time on-chain actions, followed by a single read-only RPC call (`getConfirmedTransaction`/`getTransaction`) against a bigtable-backed history node. It is fully reproducible and deterministic: any interest-bearing/scaled-UI-amount mint transfer stored and later retrieved via this code path exhibits the inconsistency, since the bug is structural rather than a crafted-corruption scenario.

### Recommendation
Persist the original `ui_amount_string` (not just `ui_amount`) in `StoredTokenAmount`, and have `From<StoredTokenAmount> for UiTokenAmount` restore it verbatim instead of recomputing it via `real_number_string_trimmed`, which is only valid for the plain (non-interest/non-scaled) case. Alternatively, store the additional interest-bearing/scaled-UI-amount extension parameters alongside the stored amount so a faithful recomputation is possible at read time.

### Proof of Concept
```rust
// storage-proto/src/lib.rs (test module)
use solana_account_decoder::parse_token::UiTokenAmount;

#[test]
fn test_stored_token_amount_interest_bearing_round_trip_inconsistency() {
    // Simulate a persisted record for an interest-bearing mint transfer:
    // original UiTokenAmount had ui_amount_string reflecting interest,
    // e.g. "1.051271096376024117", ui_amount = 1.0512710963760241
    let stored = StoredTokenAmount {
        ui_amount: 1.0512710963760241, // interest-adjusted, as originally computed
        decimals: 18,
        amount: "1000000000000000000".to_string(), // raw base amount (no interest)
    };

    let restored: UiTokenAmount = stored.into();

    // ui_amount is trusted verbatim (interest-adjusted)
    assert_eq!(restored.ui_amount, Some(1.0512710963760241));

    // ui_amount_string is recomputed from raw amount/decimals only -> "1"
    assert_eq!(restored.ui_amount_string, "1".to_string());

    // BUG: the two fields describe different quantities for the same event.
    // A faithful round trip should have ui_amount_string reflect the same
    // interest-adjusted value as ui_amount (e.g. start with "1.0512710963760241..."),
    // but it does not.
    assert_ne!(
        restored.ui_amount_string.starts_with("1.05"),
        true,
        "ui_amount_string lost interest adjustment while ui_amount kept it"
    );
}
```
Expected outcome demonstrating the bug: `restored.ui_amount` reflects the interest-adjusted value while `restored.ui_amount_string` reflects only the raw base amount — i.e., the `From<StoredTokenAmount> for UiTokenAmount` impl silently blends a stale/derived field (`ui_amount`) with a freshly and differently computed field (`ui_amount_string`), violating parse fidelity for historical bigtable-served transactions.

### Citations

**File:** storage-proto/src/lib.rs (L145-161)
```rust
impl From<StoredTokenAmount> for UiTokenAmount {
    fn from(value: StoredTokenAmount) -> Self {
        let StoredTokenAmount {
            ui_amount,
            decimals,
            amount,
        } = value;
        let ui_amount_string =
            real_number_string_trimmed(u64::from_str(&amount).unwrap_or(0), decimals);
        Self {
            ui_amount: Some(ui_amount),
            decimals,
            amount,
            ui_amount_string,
        }
    }
}
```

**File:** storage-proto/src/lib.rs (L163-177)
```rust
impl From<UiTokenAmount> for StoredTokenAmount {
    fn from(value: UiTokenAmount) -> Self {
        let UiTokenAmount {
            ui_amount,
            decimals,
            amount,
            ..
        } = value;
        Self {
            ui_amount: ui_amount.unwrap_or(0.0),
            decimals,
            amount,
        }
    }
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
