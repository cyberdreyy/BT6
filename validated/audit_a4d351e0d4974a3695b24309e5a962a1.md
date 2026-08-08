## Title
SPL-Token `*Checked` instruction `decimals` field is attacker-controlled and unvalidated, causing decoder misreporting of `tokenAmount.uiAmount` in JSON-RPC transaction parsing - (File: `transaction-status/src/parse_token.rs`)

### Summary
The Wormhole bridge bug described in the report is a "trust an untrusted precision field without validating it against the real token's canonical decimals" class of bug. Agave's transaction-status instruction parser has a direct analog: when decoding SPL-Token `TransferChecked`, `ApproveChecked`, `MintToChecked`, and `BurnChecked` instructions for `jsonParsed` RPC responses, the `decimals` value used to compute the human-readable `tokenAmount.uiAmount`/`uiAmountString` is taken verbatim from the (attacker-supplied) instruction data itself, never cross-checked against the mint account's actual `decimals` field on-chain.

### Finding Description
`parse_token` in [1](#0-0)  handles `TokenInstruction::TransferChecked { amount, decimals }` by directly constructing `SplTokenAdditionalDataV2::with_decimals(decimals)` from the instruction-supplied `decimals` byte and passing it to `token_amount_to_ui_amount_v3`, with no lookup of the referenced mint account's real `decimals` field. The same unvalidated pattern is used for `ApproveChecked` and `MintToChecked` at [2](#0-1) , and for `BurnChecked` immediately following.

`token_amount_to_ui_amount_v3` in [3](#0-2)  takes the raw `amount` and the supplied `decimals` and computes `ui_amount = amount / 10^decimals`, purely a display/derived value — it does not affect the raw on-chain `amount` field, which remains accurate.

Because `decimals` here is an arbitrary `u8` (0–255) embedded in the instruction's own byte data — not fetched from the referenced mint account's `Mint::decimals` — any user can construct a `TransferChecked`/`ApproveChecked`/`MintToChecked`/`BurnChecked` instruction whose `decimals` field diverges wildly from the mint's real precision. The instruction itself indexes the mint account only for the "mint" field in the JSON output; it never reads `Mint::decimals` from bank/account state to cross-check, unlike `get_token_account_balance`/`get_token_supply` in `rpc/src/rpc.rs`, which correctly derive decimals from the mint account via `get_mint_owner_and_additional_data` ( [4](#0-3) ).

### Impact Explanation
Any unprivileged user can submit a transaction containing a `TransferChecked`-family instruction with a bogus `decimals` value. The SPL Token program will reject/fail such a transaction at runtime if the decimals don't match the mint (`MintDecimalsMismatch`), but Agave's transaction-status parser still decodes and stores the parsed representation for the transaction (successful or failed transactions are both retrievable and parsed via `jsonParsed` encoding in RPC methods such as `getTransaction`/`getBlock`/`getConfirmedTransaction`). The resulting `tokenAmount.uiAmount`/`uiAmountString` returned to every RPC caller is silently wrong — potentially off by many orders of magnitude — for a token/mint pair that has nothing to do with the attacker's control over that mint. This is a decoder misreporting bug: the JSON-RPC response for a specific, permanently-queryable transaction permanently displays an incorrect human-readable amount, even though the underlying raw `amount` (u64) is untouched.

### Likelihood Explanation
This requires no special privileges — only the ability to submit one ordinary transaction containing a crafted SPL-Token `*Checked` instruction, which is default unprivileged behavior available to every Solana user, and the parsed/misreported output is served to any RPC caller who later queries that transaction with `jsonParsed` encoding.

### Recommendation
When parsing `TransferChecked`/`ApproveChecked`/`MintToChecked`/`BurnChecked` (and their extension variants like `TransferCheckedWithFee`), fetch the true `decimals` from the referenced mint account (as `get_token_account_balance`/`get_token_supply` already do) rather than trusting the instruction-supplied `decimals` byte, or clearly flag/validate that the instruction's asserted decimals matches on-chain mint state before using it to compute `uiAmount`.

### Proof of Concept
1. Build a transaction with a single `spl_token_2022::instruction::transfer_checked` instruction targeting a real, existing mint with e.g. 9 decimals, but set the instruction's `decimals` argument to `0` (or `255`) instead of `9`.
2. Submit the transaction (it will fail on-chain with `MintDecimalsMismatch`, but the fee payer still pays the fee and the transaction is included in a block).
3. Query the transaction via `getTransaction` with `"encoding": "jsonParsed"`.
4. Observe that `meta.err` shows failure, but the parsed instruction's `info.tokenAmount.uiAmount`/`uiAmountString` reflects the attacker-chosen `decimals` (e.g., displaying the raw integer `amount` as if it had 0 decimals) rather than the mint's true 9-decimal precision — demonstrating that the RPC misreports the value based on unvalidated instruction data.

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

**File:** transaction-status/src/parse_token.rs (L388-417)
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
                let map = value.as_object_mut().unwrap();
                parse_signers(
                    map,
                    3,
                    account_keys,
                    &instruction.accounts,
                    "owner",
                    "multisigOwner",
                );
                Ok(ParsedInstructionEnum {
                    instruction_type: "approveChecked".to_string(),
                    info: value,
                })
            }
            TokenInstruction::MintToChecked { amount, decimals } => {
                check_num_token_accounts(&instruction.accounts, 3)?;
                let additional_data = SplTokenAdditionalDataV2::with_decimals(decimals);
                let mut value = json!({
                    "mint": account_keys[instruction.accounts[0] as usize].to_string(),
                    "account": account_keys[instruction.accounts[1] as usize].to_string(),
                    "tokenAmount": token_amount_to_ui_amount_v3(amount, &additional_data),
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

**File:** rpc/src/rpc.rs (L2028-2033)
```rust
        let token_account = StateWithExtensions::<TokenAccount>::unpack(account.data())
            .map_err(|_| Error::invalid_params("Invalid param: not a Token account".to_string()))?;
        let mint = &Pubkey::from_str(&token_account.base.mint.to_string())
            .expect("Token account mint should be convertible to Pubkey");
        let (_, data) = get_mint_owner_and_additional_data(&bank, mint)?;
        let balance = token_amount_to_ui_amount_v3(token_account.base.amount, &data);
```
