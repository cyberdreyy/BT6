## Finding: `getTokenAccountBalance`/JSON-parsed token account decoding trusts stale mint account data without verifying its owner, causing misreported balances

### Root cause [1](#0-0) 

`JsonRpcRequestProcessor::get_token_account_balance` unpacks the token account, extracts its embedded `mint` pubkey, and then calls `get_mint_owner_and_additional_data` purely to obtain decimals/scaling data — the returned mint-owner is discarded (`let (_, data) = ...`): [2](#0-1) 

`get_mint_owner_and_additional_data`/`get_additional_mint_data` fetch whatever account currently lives at the `mint` address and blindly run `StateWithExtensions::<Mint>::unpack(data)` on it — **there is no check that this account is owned by an SPL Token/Token-2022 program**. Contrast this with the equivalent logic used for building transaction pre/post token balances: [3](#0-2) 

`SvmTokenInfo::unpack_token_account` explicitly requires `*mint_account.owner() == program_id` before trusting the mint's `decimals`. That ownership check is the exact analog of the missing `tokenAddress != rewardsToken` guard in the original `StakingRewards.recoverERC20` finding: one code path validates that the "recovered"/referenced resource is the expected type before trusting/using its data, while a sibling, RPC-facing path skips that validation and blindly trusts whatever bytes happen to occupy the referenced account.

`get_parsed_token_account` (used for `jsonParsed` encoding of `getAccountInfo`/`getProgramAccounts`/`getTokenAccountsByOwner`) has the same gap: [4](#0-3) 

### Why this is reachable by an unprivileged caller

SPL Token-2022 supports the `MintCloseAuthority` extension, letting a mint's owner close the mint account once its supply is zero, reclaiming rent and freeing the address for reuse — while token accounts that historically referenced that mint (with nonzero balances, or later re-associated) can still exist. Any unprivileged user can then reuse that now-empty address for an unrelated account (e.g., a new mint with different `decimals`, or any account whose raw bytes happen to satisfy the `Mint` layout). A subsequent `getTokenAccountBalance` JSON-RPC call (or `jsonParsed` `getAccountInfo`/`getTokenAccountsByOwner`) for the stale token account will decode the *new* occupant of the mint address as if it were the real mint, silently substituting its `decimals`/`scaled_ui_amount`/`interest_bearing` extensions into the balance calculation.

### Impact

This causes the RPC node to return `ui_amount`/`ui_amount_string`/`decimals` values that do not correspond to the real, on-chain economic value of the token account — a form of "wrong account data returned" reachable via a single unprivileged RPC call (`getTokenAccountBalance`, or `jsonParsed`-encoded `getAccountInfo`/`getProgramAccounts`/`getTokenAccountsByOwner`). Downstream consumers (wallets, exchanges, explorers) that rely on this RPC for displaying/computing balances can be misled about a token account's true value, which is exactly the "misreporting" class of impact called out as acceptable in the validation rules.

### Recommendation

In `get_mint_owner_and_additional_data`/`get_additional_mint_data` (and the analogous logic in `get_parsed_token_account`), verify that the fetched mint account's owner is a known SPL Token program id (mirroring the check already done in `get_token_program_id_and_mint` and `SvmTokenInfo::unpack_token_account`) before trusting its decoded `Mint` fields; if the owner check fails, return an explicit error rather than falling back to stale/attacker-influenced decimals data.

### Proof of Concept
1. Create a Token-2022 mint `M` with the `MintCloseAuthority` extension and some decimals `d1`; create a token account `A` for `M` with a nonzero balance.
2. Reduce `M`'s supply to zero and close it via `CloseAccount`, freeing the address.
3. Create a new account at the same address `M` (e.g., a new Token-2022 mint with different `decimals = d2`, or reuse it for an unrelated System account whose raw bytes still parse as `Mint`).
4. Call `getTokenAccountBalance(A)` — the RPC will unpack whatever now occupies address `M` and compute `ui_amount`/`decimals` for `A` from it, returning values inconsistent with `A`'s actual value. [5](#0-4)

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

**File:** rpc/src/parsed_token_accounts.rs (L23-49)
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
```

**File:** rpc/src/parsed_token_accounts.rs (L90-130)
```rust
/// Analyze a mint Pubkey that may be the native_mint and get the mint-account owner (token
/// program_id) and decimals
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

**File:** svm/src/transaction_balances.rs (L174-203)
```rust
impl SvmTokenInfo {
    fn unpack_token_account<CB: TransactionProcessingCallback>(
        account_loader: &mut AccountLoader<CB>,
        account: &AccountSharedData,
        index: usize,
    ) -> Option<Self> {
        let program_id = *account.owner();
        let generic_token::Account {
            mint,
            owner,
            amount,
        } = generic_token::Account::unpack(account.data(), &program_id)?;

        let mint_account = account_loader.load_account(&mint)?;
        if *mint_account.owner() != program_id {
            return None;
        }

        let generic_token::Mint { decimals, .. } =
            generic_token::Mint::unpack(mint_account.data(), &program_id)?;

        Some(Self {
            account_index: index.try_into().ok()?,
            mint,
            amount,
            owner,
            program_id,
            decimals,
        })
    }
```
