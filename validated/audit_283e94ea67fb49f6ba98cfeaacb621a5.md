### Title
JSON-RPC token-balance decoding trusts an attacker-controlled mint field without validating its owning program, allowing misreported token balances - ([File: rpc/src/parsed_token_accounts.rs])

### Summary
The RPC methods that resolve SPL-token decimals/mint metadata (`getTokenAccountBalance`, `getAccountInfo`/`getProgramAccounts`/`getTokenAccountsByOwner` with `jsonParsed` encoding) take the `mint` field embedded inside a queried token account's raw data and fetch/parse whatever account sits at that address as if it were a legitimate SPL Mint — without ever checking that the account is actually owned by a known SPL Token program. Since the `mint` field of a token account is attacker-controlled data (any account owned by the token program can contain any 32-byte value there), an attacker can point it at an arbitrary account whose bytes happen to unpack as a valid `Mint` (e.g. an account owned by their own on-chain program), thereby injecting fake `decimals`/extension config that is used to compute the reported balance/UI amount for that token account.

### Finding Description
`get_mint_owner_and_additional_data` fetches the account at the caller/embedded `mint` pubkey and immediately calls `get_additional_mint_data`, which does `StateWithExtensions::<Mint>::unpack(data)` with no check that `mint_account.owner()` is `spl_token::id()`/`spl_token_2022::id()`: [1](#0-0) [2](#0-1) 

This is invoked from `get_token_account_balance` in `rpc/src/rpc.rs`, which validates that the *token account itself* is owned by a known SPL token program, but then blindly trusts the `mint` value taken from that token account's data field and passes it, unchecked, into `get_mint_owner_and_additional_data`: [3](#0-2) 

The same unchecked pattern is used by `get_parsed_token_account`/`get_parsed_token_accounts` (used for `getAccountInfo`, `getProgramAccounts`, `getTokenAccountsByOwner`, `getTokenAccountsByDelegate` with `jsonParsed` encoding), which extract the mint pubkey directly from the raw account bytes via `get_token_account_mint` and again resolve mint data without an owner check: [4](#0-3) [5](#0-4) 

This mirrors the reported bug class precisely: a parameter (`p.tokenIn` in the external report, `mint` here) that should represent a trusted/whitelisted entity is instead taken from unvalidated, attacker-supplied data and used directly to look up and trust another account's contents.

### Impact Explanation
An unprivileged user can deploy a token account (owned by the real SPL Token program, contents otherwise fully attacker-controlled since they own/write it) whose `mint` field points at an arbitrary account they also control (owned by their own program, crafted to satisfy the 82-byte/`Mint`-with-extensions unpack layout with a forged `decimals` or interest-bearing/scaled-UI-amount extension). Querying `getTokenAccountBalance` or any `jsonParsed`-encoded account/program-accounts RPC call for that token account then returns a wrong, attacker-chosen `decimals`/`uiAmount`/`uiAmountString` for a real token account, i.e., wrong account data returned by an unprivileged JSON-RPC query — a data-integrity/misreporting issue for RPC consumers (wallets, explorers, indexers) that trust these responses at face value.

### Likelihood Explanation
Reachable with a single unprivileged JSON-RPC call (`getTokenAccountBalance`, or `getAccountInfo`/`getProgramAccounts`/`getTokenAccountsByOwner` with `encoding: jsonParsed`) against an account the caller controls; no validator/operator privilege required, and only cheap standard SPL/BPF operations (create token account, deploy a small program to write Mint-shaped bytes into a self-owned account) are needed to set up the exploit.

### Recommendation
In `get_mint_owner_and_additional_data` (and equivalently in the code path used by `get_parsed_token_account(s)`), verify that the fetched `mint_account.owner()` is a known SPL token program id (`is_known_spl_token_id`) before parsing its data as a `Mint` and using the resulting decimals/extension data to compute reported balances; treat a mismatch as "additional data unavailable" (falling back to raw/unparsed encoding) rather than trusting the data.

### Proof of Concept
1. As an unprivileged user, deploy a minimal on-chain program that owns an account and writes into it bytes matching the `spl_token::state::Mint` (or `Mint`-with-extensions) packed layout, with a forged `decimals` field (e.g. 0) — call this pubkey `FAKE_MINT`.
2. Create a normal SPL token account owned by the real `spl_token` program (via `InitializeAccount`/`InitializeAccount3` or by crafting raw account bytes if self-funding directly), but set its `mint` field to `FAKE_MINT` instead of a real mint (the runtime does not itself verify semantic linkage between a `TokenAccount.mint` field and an actual `Mint` account at that address — that consistency is only enforced by the SPL Token program during CPI-invoked instructions such as transfers, not by the raw account bytes).
3. Query `getTokenAccountBalance` (or `getAccountInfo`/`getProgramAccounts` with `encoding: jsonParsed`) for this token account through the JSON-RPC endpoint, per `rpc/src/rpc.rs` `get_token_account_balance` [3](#0-2) .
4. Observe that the returned `decimals`/`uiAmount`/`uiAmountString` reflect the forged `FAKE_MINT` data rather than being rejected, because `get_mint_owner_and_additional_data`/`get_additional_mint_data` never validate `FAKE_MINT`'s owning program [6](#0-5) .

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

**File:** account-decoder/src/parse_token.rs (L166-170)
```rust
pub fn get_token_account_mint(data: &[u8]) -> Option<Pubkey> {
    Account::valid_account_data(data)
        .then(|| Pubkey::try_from(data.get(..32)?).ok())
        .flatten()
}
```
