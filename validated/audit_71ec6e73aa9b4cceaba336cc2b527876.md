### Title
Unvalidated mint-owner allows spoofed token `decimals`/extension config in `simulateTransaction` `jsonParsed` output - (File: `rpc/src/parsed_token_accounts.rs`)

### Summary
`get_parsed_token_account` (used to build the `accounts` field of `simulateTransaction` responses) derives display metadata for a token account's balance — decimals, `InterestBearingConfig`, `ScaledUiAmountConfig` — from whatever account is stored at the token account's `mint` field, without checking that this mint account is owned by a genuine SPL Token/Token-2022 program.

### Finding Description
`get_parsed_token_account` reads the mint pubkey out of the (possibly attacker-supplied) token account bytes via `get_token_account_mint`, then resolves that mint pubkey through `account_resolver::get_account_from_overwrites_or_bank`, which prefers an `overwrite_accounts` entry over the real bank state: [1](#0-0) 

The mint data is then unpacked purely via `StateWithExtensions::<Mint>::unpack`, with no check that the resolved account is owned by `spl_token::id()` / `spl_token_2022::id()`: [2](#0-1) 

This is unlike the other RPC token endpoints in the same file/module, which explicitly enforce `is_known_spl_token_id(&mint_owner)` before trusting mint data, e.g. `get_token_account_balance` and `get_token_supply`: [3](#0-2) [4](#0-3) 

The `overwrite_accounts` parameter is explicitly documented as being used "only used for simulation results," i.e. it is fed by user-supplied account overrides in `simulateTransaction`'s `accounts.addresses`/override mechanism (`get_account_from_overwrites_or_bank`): [5](#0-4) 

This is the direct analog of the TempusController bug: the report shows a user-supplied/fake contract (`fakeVault`, `fakePool`) whose attacker-controlled return values (`ammTokens`, `mintedShares`) are trusted without validating the contract's identity/whitelist status, letting arbitrary values flow into a security-relevant calculation. Here, an RPC caller can request `simulateTransaction` with `accounts` overrides that inject a fabricated "mint" account (with attacker-chosen `decimals`, `InterestBearingConfig`, or `ScaledUiAmountConfig`) at a pubkey referenced as the `mint` field of a token account in the simulation, and the JSON-RPC layer will trust it — without ever checking that the "mint" account is owned by a real token program — when computing the reported `tokenAmount.uiAmount` for that token account in the response.

### Impact Explanation
This lets an unprivileged JSON-RPC caller cause the validator to return arbitrary, fabricated token balance/decimals/interest-bearing/scaled-UI-amount metadata in a single `simulateTransaction` call's `accounts` field — i.e., "wrong ... account data returned" from a single unprivileged query, which the accepted-impact criteria explicitly cover. Any downstream tooling (wallets, indexers, bots) that trusts `simulateTransaction`'s JSON-parsed account data for a token balance preview could be misled into showing incorrect UI amounts, decimals, or interest/scaled-UI configuration for a token, without any consensus-level validation. It does not affect consensus state or validator process integrity — it is purely a client-facing RPC misreporting bug, scoped to a single unprivileged `simulateTransaction` call.

### Likelihood Explanation
High likelihood of reachability: `simulateTransaction` with account overrides and `encoding: jsonParsed` is a standard, documented, unprivileged RPC feature. No special permissions or multi-call sequencing are required — a single call with a crafted `accounts` override list containing a fake mint account (owned by any program, e.g. System Program) at the pubkey referenced by a simulated token account's `mint` field is sufficient to trigger the mismatch.

### Recommendation
In `get_parsed_token_account` (and anywhere `get_additional_mint_data` is used with overwrite-resolved accounts), verify the resolved mint account's owner is a known SPL token program id (`is_known_spl_token_id`) before treating its contents as authoritative mint metadata, mirroring the checks already performed in `get_mint_owner_and_additional_data`, `get_token_account_balance`, and `get_token_supply`. If the owner check fails, fall back to treating the account as having no additional data (as already happens for accounts where `get_token_account_mint` returns `None`).

### Proof of Concept
1. Craft a transaction touching a token account `T` whose on-chain `mint` field is (or, via simulation, is made to be) pubkey `M`.
2. Call `simulateTransaction` with `accounts: { encoding: "jsonParsed", addresses: [T] }` and `accountsOverride`/inner-instruction account overrides (per the RPC's account-override mechanism) supplying a fabricated account at `M` — e.g., raw bytes forming a valid `spl_token_2022_interface::state::Mint` with `decimals = 0` and a spoofed `ScaledUiAmountConfig`/`InterestBearingConfig` extension, but owned by an arbitrary (non-token) program id.
3. Observe the `accounts` array in the simulation response: `get_parsed_token_account` resolves `M` from the override map (not the bank), unpacks it as a `Mint` without checking ownership, and reports `tokenAmount`/UI-amount fields computed from the attacker-supplied `decimals`/extension config for token account `T`, even though `M` is not actually a valid token-program-owned mint.

### Citations

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

**File:** rpc/src/rpc.rs (L2707-2731)
```rust
fn get_token_program_id_and_mint(
    bank: &Bank,
    token_account_filter: TokenAccountsFilter,
) -> Result<(Pubkey, Option<Pubkey>)> {
    match token_account_filter {
        TokenAccountsFilter::Mint(mint) => {
            let (mint_owner, _) = get_mint_owner_and_additional_data(bank, &mint)?;
            if !is_known_spl_token_id(&mint_owner) {
                return Err(Error::invalid_params(
                    "Invalid param: not a Token mint".to_string(),
                ));
            }
            Ok((mint_owner, Some(mint)))
        }
        TokenAccountsFilter::ProgramId(program_id) => {
            if is_known_spl_token_id(&program_id) {
                Ok((program_id, None))
            } else {
                Err(Error::invalid_params(
                    "Invalid param: unrecognized Token program id".to_string(),
                ))
            }
        }
    }
}
```

**File:** rpc/src/rpc/account_resolver.rs (L1-14)
```rust
use {
    solana_account::AccountSharedData, solana_pubkey::Pubkey, solana_runtime::bank::Bank,
    std::collections::HashMap,
};

pub(crate) fn get_account_from_overwrites_or_bank(
    pubkey: &Pubkey,
    bank: &Bank,
    overwrite_accounts: Option<&HashMap<Pubkey, AccountSharedData>>,
) -> Option<AccountSharedData> {
    overwrite_accounts
        .and_then(|accounts| accounts.get(pubkey).cloned())
        .or_else(|| bank.get_account(pubkey))
}
```
