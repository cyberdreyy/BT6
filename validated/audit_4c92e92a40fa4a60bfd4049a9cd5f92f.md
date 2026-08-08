### Title
`get_token_account_balance` trusts a token account's embedded `mint` field without verifying the mint account is owned by an SPL Token program, allowing attacker-controlled `decimals` to be returned - (File: `rpc/src/rpc.rs`, `rpc/src/parsed_token_accounts.rs`)

### Summary
`get_token_account_balance` unpacks the requested account as a `TokenAccount` and takes its `mint` field, then calls `get_mint_owner_and_additional_data` to fetch decimals for that mint, but it discards the returned mint owner and never checks `is_known_spl_token_id` on it. Because the token account's own `mint` field is attacker-controlled data (it is not cryptographically or structurally tied to any real mint account), an attacker can point it at an account they own that merely unpacks successfully as `Mint`, causing the RPC to report attacker-chosen `decimals` for `getTokenAccountBalance`.

### Finding Description
In `rpc/src/rpc.rs`, `get_token_account_balance` does: [1](#0-0) 

The function checks `is_known_spl_token_id(account.owner())` only for the queried token *account* — not for its `mint`. It then unpacks the account as `TokenAccount`, reads `token_account.base.mint` (a field fully controlled by whoever populated the account's data, since SPL token account layout does not cryptographically bind `mint` to the real mint account), and passes it straight to `get_mint_owner_and_additional_data`, discarding the returned owner Pubkey with `let (_, data) = ...`.

`get_mint_owner_and_additional_data` in `rpc/src/parsed_token_accounts.rs`: [2](#0-1) 

and the helper it calls: [3](#0-2) 

`get_additional_mint_data` calls `StateWithExtensions::<Mint>::unpack(data)` on whatever account sits at the `mint` pubkey, with no ownership check at all. If that account was created by an attacker's own program (fully attacker-controlled bytes, any owner), and its data happens to satisfy the `Mint` unpack format, `decimals` (and any interest-bearing/scaled-UI-amount extension config) from that fake account is returned and merged into the final balance response via `token_amount_to_ui_amount_v3`.

This is inconsistent with sibling functions that *do* validate mint ownership before trusting mint data — e.g. `get_token_supply`: [4](#0-3) 
and `get_token_largest_accounts`: [5](#0-4) 
and `get_token_program_id_and_mint`: [6](#0-5) 
Only `get_token_account_balance` omits the `is_known_spl_token_id(&mint_owner)` check on the derived mint owner.

### Impact Explanation
An unprivileged attacker can cause `getTokenAccountBalance` to return a token balance whose `decimals` (and thus `uiAmount`/`uiAmountString`) is computed from attacker-controlled data instead of the real SPL Mint, violating parse-fidelity/read-only invariants (returned account-derived data does not belong to a validated mint owned by an SPL Token program). This matches the "wrong account data returned" / decoder misreporting category — it is a read-only RPC data-integrity bug affecting any client that queries the crafted token account via `getTokenAccountBalance`. It does not corrupt on-chain/consensus state or crash the validator; impact is limited to client-visible misreported UI decimals/amount for that specific account.

### Likelihood Explanation
Fully attacker-feasible with no privileged access: the attacker deploys their own on-chain program, creates an account owned by that program, and writes bytes matching the `Mint`/`StateWithExtensions<Mint>` packed layout (fully within their control since they own the writing program). They then create (or already control) an SPL Token account whose `mint` field is set to this fake-mint account's pubkey, and issue a single `getTokenAccountBalance` RPC call against that token account. No special timing, staking, or validator control is required — this is reproducible deterministically every time.

### Recommendation
In `get_token_account_balance` (`rpc/src/rpc.rs`), use the mint owner returned by `get_mint_owner_and_additional_data` and reject the request if it is not a known SPL Token program id, mirroring the checks already done in `get_token_supply`, `get_token_largest_accounts`, and `get_token_program_id_and_mint`:
```rust
let (mint_owner, data) = get_mint_owner_and_additional_data(&bank, mint)?;
if !is_known_spl_token_id(&mint_owner) {
    return Err(Error::invalid_params("Invalid param: not a Token mint".to_string()));
}
```

### Proof of Concept
Integration test plan (Rust, `rpc/src/rpc.rs` test module style, using `bank.store_account`):
1. Create a "fake mint" account with an arbitrary owner (e.g. `Pubkey::new_unique()`, simulating an attacker-owned program, NOT `spl_token_interface::id()` / `spl_token_2022_interface::id()`), whose data is a validly packed `Mint` struct with `decimals = 250` (an obviously wrong/attacker-chosen value) and `is_initialized = true`.
2. Store this account via `bank.store_account(&fake_mint_pubkey, &fake_mint_account)`.
3. Create a legitimate-looking `TokenAccount` owned by `spl_token_interface::id()`, with `base.mint = fake_mint_pubkey`, `amount = 100`, `state = Initialized`.
4. Store this token account via `bank.store_account(&token_account_pubkey, &token_account)`.
5. Call `meta.get_token_account_balance(&token_account_pubkey, None)`.
6. Assert expectation: the RPC should either reject the call (e.g. `Invalid param: not a Token mint`) because the mint account is not owned by a known SPL token program, or clearly mark the balance as unverified.
7. Current (buggy) behavior: the call succeeds and returns `UiTokenAmount { decimals: 250, ... }`, proving that attacker-supplied bytes from a non-token-owned account were trusted and merged into the response.

### Citations

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

**File:** rpc/src/rpc.rs (L2037-2053)
```rust
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
```

**File:** rpc/src/rpc.rs (L2076-2087)
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
```

**File:** rpc/src/rpc.rs (L2705-2720)
```rust
/// Analyze a passed Pubkey that may be a Token program id or Mint address to determine the program
/// id and optional Mint
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
```

**File:** rpc/src/parsed_token_accounts.rs (L92-108)
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
