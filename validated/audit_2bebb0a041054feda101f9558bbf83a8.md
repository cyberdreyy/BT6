### Title
Instruction-parsing helpers trust attacker-supplied `decimals` field instead of on-chain mint data, producing misleading `tokenAmount` in `jsonParsed` transaction/instruction output - ([File: transaction-status/src/parse_token.rs])

### Summary
The SPL-Token "Checked" instruction parsers in `transaction-status/src/parse_token.rs` build the human-readable `tokenAmount` field directly from the `decimals` byte embedded in the raw instruction data, without cross-checking it against the actual decimals recorded in the referenced mint account. This mirrors the DODO M-12 pattern: a "decimal" value is taken from an untrusted, manually-supplied source and used directly in a financial-display calculation rather than being fetched from the authoritative token/mint interface.

### Finding Description
For `TransferChecked`, `ApproveChecked`, `MintToChecked`, and `BurnChecked` instructions, the parser does: [1](#0-0) [2](#0-1) [3](#0-2) 

In each case, `SplTokenAdditionalDataV2::with_decimals(decimals)` is constructed straight from the instruction's own `decimals` field — a value fully controlled by whoever crafted the instruction — and fed into `token_amount_to_ui_amount_v3` to compute `ui_amount` / `ui_amount_string` for RPC display purposes, e.g.: [4](#0-3) 

This is architecturally different from the *account* parsing path (`parse_token_v3` in `account-decoder/src/parse_token.rs`), which correctly derives decimals from the live mint account state via `get_mint_owner_and_additional_data` / `get_additional_mint_data` in `rpc/src/parsed_token_accounts.rs`: [5](#0-4) 

The SPL Token/Token-2022 program itself does validate that the `decimals` argument of a `*Checked` instruction matches the mint's real decimals before executing a transfer/approve/mint/burn — but that check happens on-chain at execution time. If the check fails, the whole transaction fails and is rolled back, yet the transaction (with its original, unmodified instruction data) is still recorded on-chain and remains fully queryable. When an unprivileged client later calls `getTransaction`/`getConfirmedTransaction` (or the `simulateTransaction` accounts/instructions rendering path) with `encoding: "jsonParsed"`, `transaction-status`'s parser re-derives `tokenAmount` purely from the instruction bytes, reproducing whatever bogus `decimals` the original submitter chose — with no re-validation against the mint.

### Impact Explanation
Any user can craft (and pay to land, even if it fails) a `TransferChecked`/`MintToChecked`/`ApproveChecked`/`BurnChecked` instruction whose `decimals` field is arbitrary (0–255) and unrelated to the referenced mint's true decimals. Every subsequent unprivileged caller of `getTransaction`/`getConfirmedTransaction` with `jsonParsed` encoding on that signature receives a `tokenAmount.ui_amount` / `ui_amount_string` computed with the attacker-chosen decimals rather than the mint's real decimals — a wrong value returned by a JSON-RPC query, which any downstream consumer (wallets, explorers, accounting tools) may treat as authoritative token amounts. This falls under "wrong data returned"/decoder misreporting from a single low-privilege call; it does not corrupt validator consensus state or crash the process.

### Likelihood Explanation
High likelihood of occurrence but low severity: no special permissions are required — a normal user pays the transaction fee, includes a `*Checked` instruction with a mismatched `decimals`, and the transaction is queryable by anyone regardless of success or failure of the on-chain instruction. The parser performs no sanity check (e.g., `checked_pow` in `token_amount_to_ui_amount_v3` silently returns `None` for very large decimals rather than panicking), so the effect is confined to display-value inaccuracy, not a crash.

### Recommendation
When parsing `*Checked` token instructions for `jsonParsed` display, do not trust the embedded `decimals` field verbatim for producing `tokenAmount`. Either (a) cross-validate the instruction's `decimals` against the referenced mint account's actual decimals (fetched the same way `get_mint_owner_and_additional_data`/`get_additional_mint_data` do for account parsing) before rendering `ui_amount`, or (b) clearly flag/omit `ui_amount`/`ui_amount_string` when the two disagree, keeping only the raw `amount` and the raw `decimals` as reported by the instruction (labeled as unverified), so downstream consumers cannot mistake an attacker-chosen decimal value for ground truth.

### Proof of Concept
1. Create any SPL-Token or Token-2022 mint `M` with real decimals `d_real` (e.g., 6).
2. Build and submit a transaction containing a `TransferChecked` (or `MintToChecked`/`ApproveChecked`/`BurnChecked`) instruction referencing mint `M`, with the `decimals` argument set to an arbitrary value `d_fake` (e.g., 0 or 9) that differs from `d_real`. The instruction will fail on-chain (SPL Token program's decimals check), but the transaction still lands in a block with a fee charged.
3. Call `getTransaction`/`getConfirmedTransaction` with `{"encoding": "jsonParsed"}` on that signature.
4. Observe that `parse_token` in `transaction-status/src/parse_token.rs` (lines 365–454) renders `tokenAmount.decimals = d_fake` and a correspondingly wrong `ui_amount`/`ui_amount_string`, computed via `token_amount_to_ui_amount_v3` (`account-decoder/src/parse_token.rs` lines 125–164), with no comparison to `M`'s real on-chain decimals — reproducing the "maker enters wrong decimal manually" flaw from the referenced report in the transaction-parsing path.

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
