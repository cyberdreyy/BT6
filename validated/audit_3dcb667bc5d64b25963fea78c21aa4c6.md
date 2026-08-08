### Title
Missing owner/type validation of the referenced mint account in RPC token-account JSON-parsing leads to misreported token balances - ([File: rpc/src/parsed_token_accounts.rs])

### Summary
The `lending.move` report's root cause is that a caller-supplied `CoinType` is never checked against the actual asset type recorded in `ReserveData`, so the contract happily treats one asset's accounting as another's. The same class of bug exists in agave's JSON-RPC token decoding path: when building the `jsonParsed` representation of an SPL token account, the code reads the `mint` pubkey out of the (attacker-influenced) account bytes and then blindly reinterprets whatever account currently lives at that address as a `Mint`, without ever checking that this account is actually owned by a legitimate SPL Token / Token-2022 program.

### Finding Description
`get_parsed_token_account` and `get_parsed_token_accounts` extract the mint address straight from the token account's raw bytes via `get_token_account_mint`, fetch that account from the bank, and pass its data directly into `get_additional_mint_data`, which unconditionally calls `StateWithExtensions::<Mint>::unpack(data)`: [1](#0-0) [2](#0-1) 

Note that `get_mint_owner_and_additional_data`/`get_additional_mint_data` never checks `mint_account.owner()` against `spl_token_interface::id()` / the Token-2022 program id before treating the bytes as a `Mint`. Contrast this with the equivalent logic in the SVM transaction-balances path, which does perform this exact check before trusting mint data: [3](#0-2) 

Because Token-2022 supports the `MintCloseAuthority` extension, a mint account can be closed (its lamports drained to zero), which frees up that address for reuse by an entirely different, attacker-controlled program that can populate the same 82+ bytes with arbitrary `decimals`/`interest_bearing_config`/`scaled_ui_amount_config` values. Any pre-existing (or new) SPL token account whose `mint` field still points at that address will then have its RPC-reported balance computed from this attacker-controlled data, since `get_additional_mint_data` performs no ownership/type check — exactly analogous to `lending.move`'s failure to verify `CoinType` against the `ReserveData` asset before trusting the numeric result.

### Impact Explanation
This is reachable through standard unprivileged JSON-RPC calls that render `jsonParsed` token accounts: `getAccountInfo`, `getProgramAccounts`, `getTokenAccountsByOwner`/`getTokenAccountsByDelegate`, and `accountSubscribe`, all of which funnel into `get_parsed_token_account`/`get_parsed_token_accounts`, used from `rpc/src/rpc.rs` and `rpc/src/rpc_subscriptions.rs`. A single such query on an affected token account returns a maliciously fabricated `decimals`, `uiAmount`, and `uiAmountString` for that balance — i.e., wrong account data returned to the caller from a single, low-rate query, which satisfies the "wrong account data returned" impact class.

### Likelihood Explanation
Exploitation requires the attacker to first close a Token-2022 mint (`MintCloseAuthority` extension) they control and reuse the freed address for a custom account under their own program before any consumer queries an outstanding token account referencing that mint. This is a multi-step but fully achievable, unprivileged sequence of ordinary Token-2022/program operations; no validator/operator privileges are needed. Because the RPC decode path has no defense-in-depth check that mirrors the one already present in `svm/src/transaction_balances.rs`, likelihood of misreporting is high once the address-reuse precondition is met.

### Recommendation
Add the same ownership/type check used in `svm/src/transaction_balances.rs::unpack_token_account` to `get_mint_owner_and_additional_data`/`get_additional_mint_data` in `rpc/src/parsed_token_accounts.rs`: verify `mint_account.owner()` is one of the recognized SPL Token program ids before unpacking its data as `Mint`, and treat mismatches the same as "mint could not be unpacked" (fall back to raw/binary encoding instead of fabricated `jsonParsed` fields).

### Proof of Concept
1. Deploy a custom program `P` and derive a PDA `M` under `P`.
2. Using `P`, `system_instruction::create_account` for `M` with owner = Token-2022 program; initialize `M` as a `Mint` with `MintCloseAuthority` set to `P`.
3. Create/keep a normal SPL token account `T` (owned by Token-2022) whose `mint` field is `M`, with some nonzero `amount`.
4. Have `P` close `M` via the mint-close-authority path (supply must be zero), draining its lamports to zero, freeing the address.
5. Have `P` immediately recreate an account at address `M` (still a PDA it controls) owned by `P` itself, and write arbitrary bytes shaped like a `Mint` (`decimals = 0`, or corrupted extension TLVs) into it.
6. Call `getTokenAccountsByOwner`/`getAccountInfo` on `T` with `encoding: "jsonParsed"`. The RPC response's `tokenAmount.decimals`/`uiAmount` will reflect the attacker-controlled bytes at `M` rather than genuine mint state, because `get_additional_mint_data` never checked that `M` is still owned by a real token program. [4](#0-3)

### Citations

**File:** rpc/src/parsed_token_accounts.rs (L23-41)
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
