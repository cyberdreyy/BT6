### Title
Parsed `transfer`/`transferChecked` instructions misreport delivered token amount for Token-2022 mints with `TransferFeeConfig` (fee-on-transfer analog) - (File: `transaction-status/src/parse_token.rs`)

### Summary
The Sherlock report describes a router that reports/enforces the *requested* transfer amount without accounting for a fee-on-transfer token silently deducting a fee, so the recipient receives less than what is reported/expected. The closest reachable analog in agave is the JSON-RPC parsed-instruction decoder for SPL Token / Token-2022 instructions: for plain `Transfer` and `TransferChecked` instructions, the decoder reports the raw instruction `amount`/`tokenAmount` field as if it were the amount delivered to the destination, with no way to represent a fee, even though Token-2022's `TransferFeeConfig` mint extension causes the token program to automatically withhold a fee on transfers.

### Finding Description
`parse_token()` in [1](#0-0)  handles the deprecated `Transfer` instruction, and [2](#0-1)  handles `TransferChecked`. Both simply echo the instruction's `amount`/`tokenAmount` argument into the JSON `info` object with no fee field.

By contrast, the dedicated `TransferCheckedWithFee` variant explicitly reports both `tokenAmount` and a separate `feeAmount`, as implemented in [3](#0-2) , confirmed by the test expectations at [4](#0-3)  where `tokenAmount` (55) and `feeAmount` (5) are reported as distinct fields (the destination actually receives 50).

Token-2022's `TransferFeeConfig` extension (surfaced elsewhere in agave via [5](#0-4)  and [6](#0-5) ) causes the token program to withhold a fee on transfers for mints configured with it. Whenever a client uses ordinary `Transfer`/`TransferChecked` (not `TransferCheckedWithFee`) against such a mint, the parser in `parse_token.rs` has no mechanism to express or flag that a fee will be/was withheld — it always reports the full instruction `amount` as the `tokenAmount`, identical to how the router in the report treats the pre-fee amount as the actual amount received.

This is purely a decoding/misreporting issue in the `jsonParsed` transaction/instruction decoder path used by JSON-RPC methods such as `getTransaction`/`getConfirmedTransaction`/`getParsedBlock` (unprivileged, any RPC caller), not a runtime/consensus bug — actual on-chain token balances (`preTokenBalances`/`postTokenBalances`) are computed independently from live account state and are unaffected.

### Impact Explanation
Any unprivileged RPC consumer (wallets, indexers, exchanges, explorers) requesting `jsonParsed` encoding for a transaction containing a `transfer`/`transferChecked` instruction on a Token-2022 mint with an active `TransferFeeConfig` extension will receive a `tokenAmount` value that overstates the amount actually credited to the destination account, because the withheld fee is never reflected in the parsed instruction `info`. Consumers that rely on parsed instruction amounts (rather than diffing `pre`/`postTokenBalances`) for accounting, reconciliation, or fraud detection can be misled about actual value transferred. This is a decoder misreporting issue rather than a consensus or crash bug.

### Likelihood Explanation
This triggers deterministically and with certainty whenever a Token-2022 mint has `TransferFeeConfig` configured and a `Transfer`/`TransferChecked` instruction (as opposed to `TransferCheckedWithFee`) moves tokens for that mint — a common, unprivileged, everyday interaction. No special access or attacker action is required; it is a systemic gap in the decoder for a widely-used Token-2022 extension.

### Recommendation
When parsing `Transfer`/`TransferChecked` instructions, if the referenced mint has an active `TransferFeeConfig` extension, either (a) surface a warning/flag indicating a fee will be withheld and the reported `tokenAmount` is the pre-fee amount, or (b) compute and include a `feeAmount` field consistent with the `TransferCheckedWithFee` output shape, using the mint's current `TransferFeeConfig` (analogous to how `account-decoder/src/parse_token_extension.rs` already surfaces `TransferFeeConfig`/`TransferFeeAmount` for account/mint decoding). This keeps `transfer`/`transferChecked` parsing consistent with `transferCheckedWithFee` and prevents consumers from assuming the full instruction amount reaches the destination.

### Proof of Concept
1. Create a Token-2022 mint with the `TransferFeeConfig` extension enabled (e.g., 5% fee).
2. Submit a `TransferChecked` instruction (not `TransferCheckedWithFee`) moving `amount = 100` tokens from source to destination.
3. Call `getTransaction`/`getConfirmedTransaction` with `encoding: jsonParsed`; the parsed instruction (per `transaction-status/src/parse_token.rs:365-387`) reports `tokenAmount.amount == "100"` with no fee field.
4. Compare against `postTokenBalances`/`preTokenBalances` for the destination account: the actual credited amount will be `100 - fee` (e.g., 95), diverging from the reported `tokenAmount`, exactly mirroring the fee-on-transfer discrepancy described in the source report.

### Citations

**File:** transaction-status/src/parse_token.rs (L158-179)
```rust
            #[allow(deprecated)]
            TokenInstruction::Transfer { amount } => {
                check_num_token_accounts(&instruction.accounts, 3)?;
                let mut value = json!({
                    "source": account_keys[instruction.accounts[0] as usize].to_string(),
                    "destination": account_keys[instruction.accounts[1] as usize].to_string(),
                    "amount": amount.to_string(),
                });
                let map = value.as_object_mut().unwrap();
                parse_signers(
                    map,
                    2,
                    account_keys,
                    &instruction.accounts,
                    "authority",
                    "multisigAuthority",
                );
                Ok(ParsedInstructionEnum {
                    instruction_type: "transfer".to_string(),
                    info: value,
                })
            }
```

**File:** transaction-status/src/parse_token.rs (L365-387)
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
                let map = value.as_object_mut().unwrap();
                parse_signers(
                    map,
                    3,
                    account_keys,
                    &instruction.accounts,
                    "authority",
                    "multisigAuthority",
                );
                Ok(ParsedInstructionEnum {
                    instruction_type: "transferChecked".to_string(),
                    info: value,
                })
            }
```

**File:** transaction-status/src/parse_token/extension/transfer_fee.rs (L44-71)
```rust
        TransferFeeInstruction::TransferCheckedWithFee {
            amount,
            decimals,
            fee,
        } => {
            check_num_token_accounts(account_indexes, 4)?;
            let additional_data = SplTokenAdditionalDataV2::with_decimals(decimals);
            let mut value = json!({
                "source": account_keys[account_indexes[0] as usize].to_string(),
                "mint": account_keys[account_indexes[1] as usize].to_string(),
                "destination": account_keys[account_indexes[2] as usize].to_string(),
                "tokenAmount": token_amount_to_ui_amount_v3(amount, &additional_data),
                "feeAmount": token_amount_to_ui_amount_v3(fee, &additional_data),
            });
            let map = value.as_object_mut().unwrap();
            parse_signers(
                map,
                3,
                account_keys,
                account_indexes,
                "authority",
                "multisigAuthority",
            );
            Ok(ParsedInstructionEnum {
                instruction_type: "transferCheckedWithFee".to_string(),
                info: value,
            })
        }
```

**File:** transaction-status/src/parse_token/extension/transfer_fee.rs (L262-283)
```rust
            ParsedInstructionEnum {
                instruction_type: "transferCheckedWithFee".to_string(),
                info: json!({
                    "source": account_pubkey.to_string(),
                    "mint": mint_pubkey.to_string(),
                    "destination": recipient.to_string(),
                    "authority": owner.to_string(),
                    "tokenAmount": {
                        "uiAmount": 0.55,
                        "decimals": 2,
                        "amount": "55",
                        "uiAmountString": "0.55",
                   },
                    "feeAmount": {
                        "uiAmount": 0.05,
                        "decimals": 2,
                        "amount": "5",
                        "uiAmountString": "0.05",
                   },
                })
            }
        );
```

**File:** account-decoder/src/parse_token_extension.rs (L166-198)
```rust
fn convert_transfer_fee(transfer_fee: extension::transfer_fee::TransferFee) -> UiTransferFee {
    UiTransferFee {
        epoch: u64::from(transfer_fee.epoch),
        maximum_fee: u64::from(transfer_fee.maximum_fee),
        transfer_fee_basis_points: u16::from(transfer_fee.transfer_fee_basis_points),
    }
}

fn convert_transfer_fee_config(
    transfer_fee_config: extension::transfer_fee::TransferFeeConfig,
) -> UiTransferFeeConfig {
    let transfer_fee_config_authority: Option<Pubkey> =
        transfer_fee_config.transfer_fee_config_authority.into();
    let withdraw_withheld_authority: Option<Pubkey> =
        transfer_fee_config.withdraw_withheld_authority.into();

    UiTransferFeeConfig {
        transfer_fee_config_authority: transfer_fee_config_authority
            .map(|pubkey| pubkey.to_string()),
        withdraw_withheld_authority: withdraw_withheld_authority.map(|pubkey| pubkey.to_string()),
        withheld_amount: u64::from(transfer_fee_config.withheld_amount),
        older_transfer_fee: convert_transfer_fee(transfer_fee_config.older_transfer_fee),
        newer_transfer_fee: convert_transfer_fee(transfer_fee_config.newer_transfer_fee),
    }
}

fn convert_transfer_fee_amount(
    transfer_fee_amount: extension::transfer_fee::TransferFeeAmount,
) -> UiTransferFeeAmount {
    UiTransferFeeAmount {
        withheld_amount: u64::from(transfer_fee_amount.withheld_amount),
    }
}
```

**File:** account-decoder-client-types/src/token.rs (L253-269)
```rust
#[derive(Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct UiTransferFeeConfig {
    pub transfer_fee_config_authority: Option<String>,
    pub withdraw_withheld_authority: Option<String>,
    pub withheld_amount: u64,
    pub older_transfer_fee: UiTransferFee,
    pub newer_transfer_fee: UiTransferFee,
}

#[derive(Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct UiTransferFee {
    pub epoch: u64,
    pub maximum_fee: u64,
    pub transfer_fee_basis_points: u16,
}
```
