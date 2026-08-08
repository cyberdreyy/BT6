### Title
`TokenInstruction::TransferChecked`/`ApproveChecked`/`MintToChecked`/`BurnChecked` parsing trusts unverified instruction-embedded `decimals`, producing misreported UI token amounts - ([File: transaction-status/src/parse_token.rs])

### Summary

### Finding Description
The report's root-cause pattern is "a decimals value used to scale a raw token amount is not validated against the true/canonical decimals for that token, producing wrong displayed values." Agave's transaction-status instruction parser exhibits the same class of bug for SPL Token / Token-2022 "checked" instructions.

When `parse_token` decodes `TokenInstruction::TransferChecked`, `ApproveChecked`, `MintToChecked`, or `BurnChecked`, it builds the reported `tokenAmount` purely from the `decimals` byte embedded in the instruction data supplied by the transaction's author — it never cross-checks this value against the actual mint account's `decimals` field: [1](#0-0) 

The same pattern repeats for `ApproveChecked`, `MintToChecked`, and `BurnChecked`: [2](#0-1) [3](#0-2) [4](#0-3) 

`token_amount_to_ui_amount_v3` blindly scales the raw `u64` amount using whatever `decimals` value it is given: [5](#0-4) 

By contrast, the account-decoding path (`parse_token_v3` / `get_token_account_balance` / `get_token_supply`) correctly fetches the mint's real decimals from bank state via `get_mint_owner_and_additional_data`/`get_additional_mint_data` before computing a UI amount: [6](#0-5) 

The Token/Token-2022 program itself validates at execution time that the `decimals` argument in a "checked" instruction matches the mint's actual decimals, and fails the instruction otherwise. However, Agave's transaction-status parser (used by `getTransaction`, `getConfirmedTransaction`, and pubsub transaction notifications with `jsonParsed` encoding) parses instruction data independent of execution outcome and without consulting the mint account, so it will happily construct and return a `UiTokenAmount` computed from an attacker-chosen, unvalidated `decimals` value — including for failed transactions, or for accounts whose true mint decimals differ.

### Impact Explanation
Any user can craft a transaction containing a `TransferChecked`-family instruction with an arbitrary `decimals` byte (0-255) that mismatches the real mint. The instruction may fail on-chain, but RPC consumers requesting `jsonParsed` transaction data will still receive a `tokenAmount.uiAmount` / `uiAmountString` computed with the bogus decimals — this is wrong-value/misreporting returned by an RPC read, matching the "decoder panic and misreporting" acceptable-impact category. Downstream tooling, block explorers, and wallets relying on this parsed field can display grossly incorrect balances/amounts (e.g., a transfer of `100` raw units reported as `100` whole tokens instead of `0.0000000001`, or vice versa), which is directly analogous to the divisibility/precision-loss issue described in the external report.

### Likelihood Explanation
Likelihood is high for triggering the misreporting: it requires only a single, ordinary transaction submission (no special privileges) containing a checked SPL-Token instruction with a `decimals` field different from the target mint's actual value, followed by a standard `getTransaction`/`getConfirmedTransaction`/`transactionSubscribe` request with `jsonParsed` encoding.

### Recommendation
When parsing `TransferChecked`/`ApproveChecked`/`MintToChecked`/`BurnChecked` (and any other instruction that carries a caller-supplied `decimals`), the parser should not treat the instruction-embedded `decimals` as authoritative for producing `UiTokenAmount`. Where bank/mint state is available (as it already is in the sibling `account-decoder` code path), the parser should look up and use the real mint decimals, or clearly flag/annotate when the instruction's declared decimals do not match the actual mint, so that misleading `ui_amount`/`ui_amount_string` values are not silently returned to RPC clients.

### Proof of Concept
1. Create an SPL Token-2022 mint `M` with `decimals = 9`.
2. Submit a transaction containing a `TransferChecked` instruction referencing mint `M`, `amount = 1_000_000_000`, but with `decimals = 0` (mismatched vs. mint's actual `9`). The instruction fails at runtime due to the token program's decimals check, but the transaction is still included/recorded.
3. Query the transaction via `getTransaction` with `{"encoding":"jsonParsed"}`.
4. Observe `meta.transaction.message.instructions[i].parsed.info.tokenAmount` shows `uiAmount: 1000000000`, `uiAmountString: "1000000000"` (as if `decimals=0`), rather than the mint-correct `uiAmount: 1.0` — demonstrating the misreported value driven entirely by attacker-controlled instruction data at `transaction-status/src/parse_token.rs:365-373`.

### Citations

**File:** transaction-status/src/parse_token.rs (L365-373)
```rust
            TokenInstruction::TransferChecked { amount, decimals } => {
                check_num_token_accounts(&instruction.accounts, 4)?;
                let additional_data = SplTokenAdditionalDataV2::with_decimals(decimals);
                let mut value = json!({
                    "source": account_keys[instruction.accounts[0] as usize].to_string(),
                    "mint": account_keys[instruction.accounts[1] as usize].to_string(),
                    "destination": account_keys[instruction.accounts[2] as usize].to_string(),
                    "tokenAmount": token_amount_to_ui_amount_v3(amount, &additional_data),
                });
```

**File:** transaction-status/src/parse_token.rs (L388-396)
```rust
            TokenInstruction::ApproveChecked { amount, decimals } => {
                check_num_token_accounts(&instruction.accounts, 4)?;
                let additional_data = SplTokenAdditionalDataV2::with_decimals(decimals);
                let mut value = json!({
                    "source": account_keys[instruction.accounts[0] as usize].to_string(),
                    "mint": account_keys[instruction.accounts[1] as usize].to_string(),
                    "delegate": account_keys[instruction.accounts[2] as usize].to_string(),
                    "tokenAmount": token_amount_to_ui_amount_v3(amount, &additional_data),
                });
```

**File:** transaction-status/src/parse_token.rs (L411-418)
```rust
            TokenInstruction::MintToChecked { amount, decimals } => {
                check_num_token_accounts(&instruction.accounts, 3)?;
                let additional_data = SplTokenAdditionalDataV2::with_decimals(decimals);
                let mut value = json!({
                    "mint": account_keys[instruction.accounts[0] as usize].to_string(),
                    "account": account_keys[instruction.accounts[1] as usize].to_string(),
                    "tokenAmount": token_amount_to_ui_amount_v3(amount, &additional_data),
                });
```

**File:** transaction-status/src/parse_token.rs (L433-440)
```rust
            TokenInstruction::BurnChecked { amount, decimals } => {
                check_num_token_accounts(&instruction.accounts, 3)?;
                let additional_data = SplTokenAdditionalDataV2::with_decimals(decimals);
                let mut value = json!({
                    "account": account_keys[instruction.accounts[0] as usize].to_string(),
                    "mint": account_keys[instruction.accounts[1] as usize].to_string(),
                    "tokenAmount": token_amount_to_ui_amount_v3(amount, &additional_data),
                });
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
