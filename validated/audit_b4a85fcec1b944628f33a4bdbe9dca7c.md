### Title
JSON-parsed `TransferChecked`/`ApproveChecked`/`MintToChecked`/`BurnChecked` token instructions trust attacker-supplied `decimals` instead of the mint's actual decimals, producing misreported `tokenAmount.uiAmount` via RPC - (File: `transaction-status/src/parse_token.rs`)

### Summary
The `parse_token` instruction parser, used by `jsonParsed`-encoding JSON-RPC endpoints (`getTransaction`, `getConfirmedTransaction`, `getBlock`, `simulateTransaction`'s inner instructions, etc.), computes the human-readable `tokenAmount` (including `uiAmount`/`uiAmountString`) for SPL Token `*Checked` instructions solely from the `decimals` value embedded in the raw, user-supplied instruction data — never cross-checked against the actual decimals recorded on the referenced mint account.

### Finding Description
`parse_token` in `transaction-status/src/parse_token.rs` decodes `TokenInstruction::TransferChecked { amount, decimals }`, `ApproveChecked { amount, decimals }`, `MintToChecked { amount, decimals }`, and `BurnChecked { amount, decimals }` and immediately builds the additional data purely from the instruction's own `decimals` field: [1](#0-0) 
This mirrors the Axelar bug pattern: a value (decimals/amount) supplied at one "layer" (the raw instruction/payload) is trusted and propagated into a user-facing representation (`ui_amount`) without validating it against the authoritative source of truth (the mint account's actual `decimals` field, analogous to the "remote chain's" token decimals in the Axelar report). The parser never loads or unpacks the mint account referenced by `instruction.accounts[1]` to verify `decimals` matches `Mint.decimals`; it only checks account-index bounds via `check_num_token_accounts`. [2](#0-1) 

For comparison, the *authoritative* decoders that read live on-chain token-account/mint state (`account-decoder/src/parse_token.rs::token_amount_to_ui_amount_v3` fed by `SplTokenAdditionalDataV2` derived from the actual `Mint` account, used in `rpc/src/rpc.rs::get_token_account_balance` / `get_token_supply`) do use the real mint decimals: [3](#0-2) [4](#0-3) 

By contrast, the *instruction* parser used for transaction/block history has no such cross-check and simply echoes back whatever `decimals` the transaction's instruction data contained: [5](#0-4) 

This code path is reachable by any unprivileged RPC caller: `parse_token` is registered for every SPL Token/Token-2022 program id and invoked from the generic instruction dispatcher used by `jsonParsed` transaction/block encoding: [6](#0-5) [7](#0-6) 

### Impact Explanation
On-chain, the SPL Token program itself validates that a `*Checked` instruction's `decimals` field matches the referenced mint's `decimals`, and rejects the instruction (and thus the whole atomic transaction) if they differ. However, `parse_token` parses and renders the `tokenAmount`/`uiAmount` for **every** instruction it encounters when historical/simulated transactions are decoded via `jsonParsed` encoding — including instructions belonging to failed transactions (and inner instructions surfaced by `simulateTransaction`), for which the `decimals` mismatch is exactly what caused the failure. In those cases, a caller can craft an instruction with an arbitrary (wrong) `decimals` value, and any RPC client, block explorer, indexer, or wallet parsing `getTransaction`/`getBlock`/`simulateTransaction` output for that transaction will display a `tokenAmount.uiAmount`/`uiAmountString` computed from the attacker-chosen decimals rather than the token's real decimals — a wrong-data-returned/misreporting condition from a single unprivileged RPC call, matching the "decoder ... misreporting" impact category.

### Likelihood Explanation
High likelihood of triggering the display bug: any user can submit (or have the RPC simulate) a transaction containing a `TransferChecked`/`ApproveChecked`/`MintToChecked`/`BurnChecked` instruction whose `decimals` field intentionally differs from the referenced mint's actual decimals. The instruction will fail on-chain (or during simulation) due to the SPL Token program's own decimals check, but the failed transaction/instruction is still retrievable and rendered by any `jsonParsed`-encoding RPC call, so the misreporting is trivially reproducible with a single low-cost RPC/transaction interaction and no special privileges.

### Recommendation
When parsing `TransferChecked`/`ApproveChecked`/`MintToChecked`/`BurnChecked` (and any other instruction where `decimals` is instruction-supplied rather than derived from chain state) for `jsonParsed` output, either: (1) clearly mark the derived `uiAmount` as "unverified" for failed/simulated instructions, or (2) cross-check the instruction's `decimals` against the actual mint account's `decimals` (loaded via the same account-fetch path already used by `get_mint_owner_and_additional_data`/`get_additional_mint_data`) before rendering `tokenAmount`, falling back to raw `amount` only when a mismatch is detected, so RPC consumers cannot be shown a UI amount computed from an unvalidated/attacker-controlled decimals value.

### Proof of Concept
1. Create (or have any user submit) a transaction containing a Token-2022/SPL-Token `TransferChecked` instruction where the `decimals` field encoded in the instruction data (e.g., `0`) does not match the actual `Mint.decimals` of the referenced mint account (e.g., `9`).
2. Submit the transaction; the SPL Token program's on-chain decimals check causes the instruction (and transaction) to fail.
3. Call `getTransaction`/`getConfirmedTransaction` (or `simulateTransaction` for inner instructions) with `encoding: "jsonParsed"` for that transaction signature.
4. Observe that `transaction-status/src/parse_token.rs`'s `parse_token` still returns a `tokenAmount` object (`ui_amount`, `ui_amount_string`, `decimals`) computed strictly from the attacker-supplied `decimals` value in the instruction data, as shown by `token_amount_to_ui_amount_v3(amount, &additional_data)` where `additional_data` is built exclusively from `SplTokenAdditionalDataV2::with_decimals(decimals)` (the instruction's own field), confirmed by the existing unit tests exercising exactly this code path: [1](#0-0) [8](#0-7)

### Citations

**File:** transaction-status/src/parse_token.rs (L30-43)
```rust
pub fn parse_token(
    instruction: &CompiledInstruction,
    account_keys: &AccountKeys,
) -> Result<ParsedInstructionEnum, ParseInstructionError> {
    match instruction.accounts.iter().max() {
        Some(index) if (*index as usize) < account_keys.len() => {}
        _ => {
            // Runtime should prevent this from ever happening
            return Err(ParseInstructionError::InstructionKeyMismatch(
                ParsableProgram::SplToken,
            ));
        }
    }
    if let Ok(token_instruction) = TokenInstruction::unpack(&instruction.data) {
```

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

**File:** transaction-status/src/parse_token.rs (L1558-1585)
```rust
        let message = Message::new(&[transfer_ix], None);
        let compiled_instruction = &message.instructions[0];
        assert_eq!(
            parse_token(
                compiled_instruction,
                &AccountKeys::new(&message.account_keys, None)
            )
            .unwrap(),
            ParsedInstructionEnum {
                instruction_type: "transferChecked".to_string(),
                info: json!({
                    "source": account_pubkey.to_string(),
                    "destination": recipient.to_string(),
                    "mint": mint_pubkey.to_string(),
                    "multisigAuthority": multisig_pubkey.to_string(),
                    "signers": vec![
                        multisig_signer0.to_string(),
                        multisig_signer1.to_string(),
                    ],
                    "tokenAmount": {
                        "uiAmount": 0.42,
                        "decimals": 2,
                        "amount": "42",
                        "uiAmountString": "0.42",
                   }
                })
            }
        );
```

**File:** rpc/src/rpc.rs (L2013-2034)
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

**File:** transaction-status/src/parse_instruction.rs (L26-56)
```rust
static PARSABLE_PROGRAM_IDS: std::sync::LazyLock<HashMap<Pubkey, ParsableProgram>> =
    std::sync::LazyLock::new(|| {
        [
            (
                address_lookup_table::id(),
                ParsableProgram::AddressLookupTable,
            ),
            (
                spl_associated_token_account_interface::program::id(),
                ParsableProgram::SplAssociatedTokenAccount,
            ),
            (spl_memo_interface::v1::id(), ParsableProgram::SplMemo),
            (spl_memo_interface::v3::id(), ParsableProgram::SplMemo),
            (spl_memo_interface::v4::id(), ParsableProgram::SplMemo),
            (solana_sdk_ids::bpf_loader::id(), ParsableProgram::BpfLoader),
            (
                solana_sdk_ids::bpf_loader_upgradeable::id(),
                ParsableProgram::BpfUpgradeableLoader,
            ),
            (stake::id(), ParsableProgram::Stake),
            (system_program::id(), ParsableProgram::System),
            (vote::id(), ParsableProgram::Vote),
        ]
        .into_iter()
        .chain(
            spl_token_ids()
                .into_iter()
                .map(|spl_token_id| (spl_token_id, ParsableProgram::SplToken)),
        )
        .collect()
    });
```

**File:** transaction-status/src/parse_instruction.rs (L96-100)
```rust
pub fn parse(
    program_id: &Pubkey,
    instruction: &CompiledInstruction,
    account_keys: &AccountKeys,
    stack_height: Option<u32>,
```
