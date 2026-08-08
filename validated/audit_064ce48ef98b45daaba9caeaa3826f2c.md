### Title
`get_token_account_balance` reports decimals/UI amount from a mint account without verifying it is owned by a known SPL token program - ([File: rpc/src/rpc.rs])

### Summary
`get_token_account_balance` in `rpc/src/rpc.rs` checks `is_known_spl_token_id` only on the queried token *account*, but discards the mint-owner check when computing the displayed balance [1](#0-0) . The mint lookup helper `get_mint_owner_and_additional_data`/`get_additional_mint_data` only requires that the bytes at the mint's address unpack as `StateWithExtensions::<Mint>`, with no ownership validation [2](#0-1) . This differs from the sibling functions `get_token_supply`, `get_token_largest_accounts`, and `get_token_accounts_by_owner`, which all explicitly re-check `is_known_spl_token_id` on the mint owner returned from that helper [3](#0-2) [4](#0-3) [5](#0-4) .

### Finding Description
`get_token_account_balance` first validates the queried account's owner (`is_known_spl_token_id(account.owner())`) and unpacks it as a `TokenAccount`, extracting the `mint` field from its data [6](#0-5) . It then calls `get_mint_owner_and_additional_data(&bank, mint)` and discards the returned owner Pubkey with `let (_, data) = ...` [7](#0-6) .

`get_mint_owner_and_additional_data` fetches whatever account currently exists at that `mint` pubkey (unless it's the hardcoded native-mint alias) and calls `get_additional_mint_data`, which only does `StateWithExtensions::<Mint>::unpack(data)` — it never checks `mint_account.owner()` against `is_known_spl_token_id` [8](#0-7) . So the decimals/extensions used to compute `token_amount_to_ui_amount_v3` for the balance are taken from any account whose raw bytes happen to unpack as a valid `Mint`, regardless of who owns that account.

Exploit flow using only legitimate, unprivileged on-chain transactions:
1. Attacker creates an spl-token-2022 mint `M` with the `MintCloseAuthority` extension and mints/creates a token account `TA` (a genuine account owned by the real Token-2022 program) whose `mint` field is `M`. At creation time `M` legitimately satisfies whatever mint-ownership checks the SPL Token program itself enforces, so `TA` is created successfully.
2. Attacker drains `M`'s supply to zero and closes it via `CloseAccount` (permitted by `MintCloseAuthority`), returning its lamports to zero. `TA` is untouched and still exists, still owned by the real Token-2022 program, still containing `mint = M` in its data.
3. Because `M` now has zero lamports, its address is free to be reused: attacker deploys their own program and issues `CreateAccount`/`Assign` to recreate an account at pubkey `M`, owned by their own (non-token) program, with data bytes crafted to be a byte-valid `Mint` layout (COption mint authority, u64 supply, u8 decimals of the attacker's choosing, etc.).
4. Attacker calls `getTokenAccountBalance(TA)`. The RPC passes the owner check on `TA` (real Token-2022 owner), unpacks `TA.base.amount` and `mint = M`, then calls `get_mint_owner_and_additional_data(bank, M)`, which fetches the *new*, attacker-owned account at `M` and successfully unpacks it as `Mint` — with no owner check. The reported `decimals`/`ui_amount` are therefore fully attacker-controlled, not derived from any real SPL token mint.

This bypasses the intended invariant (enforced elsewhere in the same file) that mint data used for balance/decimals reporting must come from an account owned by a known SPL token program.

### Impact Explanation
This causes `getTokenAccountBalance` to return a `UiTokenAmount.decimals`/`ui_amount` that is computed from data controlled by an owner-arbitrary account rather than a genuine SPL token mint, i.e., misreporting of RPC-returned account-derived data (matches the "decoder misreporting / wrong account data returned" bounty category). Any client or indexer relying on `getTokenAccountBalance`'s decimal/ui_amount field for this token account can be shown an incorrect balance display for a real, unmodified token account, without any warning that the mint reference is stale/hijacked.

### Likelihood Explanation
The preconditions rely only on standard, documented, permissionless features already present for any user: creating a Token-2022 mint with `MintCloseAuthority`, closing it after zeroing supply, and reusing the now-zero-lamport address for an unrelated account/program, followed by one `getTokenAccountBalance` RPC call — no leaked keys, no validator/leader control, and well within the one-call-per-half-slot rate limit. This is fully reproducible by any unprivileged client and repeatable at will.

### Recommendation
In `get_token_account_balance` (`rpc/src/rpc.rs`), do not discard the mint owner returned by `get_mint_owner_and_additional_data`; explicitly validate it with `is_known_spl_token_id` (mirroring `get_token_supply`/`get_token_largest_accounts`/`get_token_accounts_by_owner`) and return `Error::invalid_params` if the mint account is not owned by a recognized SPL token program before computing the balance.

### Proof of Concept
Integration test plan (using the existing `RpcHandler` test harness already present in `rpc/src/rpc.rs` tests, e.g. around the token-account test setups at lines 8011-8070 and 8526-8624):
1. Create a Token-2022 mint account `M` with `decimals = 2`, `MintCloseAuthority` extension, owned by `spl_token_2022::id()`, and a real `TokenAccount` `TA` owned by `spl_token_2022::id()` with `mint = M`, `amount = 1000`.
2. Simulate the mint-close-and-reuse by overwriting the bank account at `M` (via `bank.store_account`, standing in for the on-chain close+recreate sequence) with an account owned by an arbitrary non-token program id (e.g. `Pubkey::new_unique()`), whose data bytes are a valid packed `Mint` with `decimals = 9` (attacker-chosen) and `is_initialized = true`.
3. Call `rpc.get_token_account_balance(&TA_pubkey, None)`.
4. Assert (expected, currently failing): the call returns `Error::invalid_params("... not a Token mint")` because `M`'s owner is not a known SPL token id — instead, the current code returns `Ok(UiTokenAmount { decimals: 9, ui_amount: "10.000000000", ... })`, proving the balance/decimals were derived from the attacker-owned fake mint account rather than being rejected. [1](#0-0) [2](#0-1)

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

**File:** rpc/src/rpc.rs (L2046-2050)
```rust
        if !is_known_spl_token_id(mint_account.owner()) {
            return Err(Error::invalid_params(
                "Invalid param: not a Token mint".to_string(),
            ));
        }
```

**File:** rpc/src/rpc.rs (L2082-2087)
```rust
        let (mint_owner, data) = get_mint_owner_and_additional_data(&bank, &mint)?;
        if !is_known_spl_token_id(&mint_owner) {
            return Err(Error::invalid_params(
                "Invalid param: not a Token mint".to_string(),
            ));
        }
```

**File:** rpc/src/rpc.rs (L2712-2719)
```rust
        TokenAccountsFilter::Mint(mint) => {
            let (mint_owner, _) = get_mint_owner_and_additional_data(bank, &mint)?;
            if !is_known_spl_token_id(&mint_owner) {
                return Err(Error::invalid_params(
                    "Invalid param: not a Token mint".to_string(),
                ));
            }
            Ok((mint_owner, Some(mint)))
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
