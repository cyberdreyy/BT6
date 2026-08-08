### Title
`getParsedAccountInfo`/`getProgramAccounts(jsonParsed)`/`accountSubscribe(jsonParsed)` misreport SPL token decimals/UI amounts because the mint's owning program is never validated - ([File: rpc/src/parsed_token_accounts.rs])

### Summary
When RPC handlers decode SPL token accounts with `jsonParsed` encoding, they resolve the token account's embedded `mint` field and fetch that account's data to compute decimals/UI-amount formatting via `get_mint_owner_and_additional_data`. Unlike the analogous `getTokenSupply`/`getTokenLargestAccounts`/`getTokenAccountsByOwner` paths, this helper's caller in `get_parsed_token_accounts`/`get_parsed_token_account` never checks that the returned mint-account owner is actually a known SPL Token program (`is_known_spl_token_id`) before using its unpacked `Mint` data to compute the displayed `decimals`/`uiAmount`.

### Finding Description
`get_mint_owner_and_additional_data` in [1](#0-0)  loads whatever account resides at the `mint` pubkey extracted from the token account bytes and, if its data happens to unpack as a valid `Mint` layout, returns `(*mint_account.owner(), mint_data)` unconditionally — with no verification that `mint_account.owner()` is `spl_token::id()` or `spl_token_2022::id()`.

This helper is consumed directly by `get_parsed_token_accounts` (used for `jsonParsed` in `getProgramAccounts`/`getTokenAccountsByOwner`/`getTokenAccountsByDelegate`) and `get_parsed_token_account` (used for `getAccountInfo`/`accountSubscribe` jsonParsed and simulation results) at [2](#0-1) . Both call sites feed the returned decimals straight into `AccountAdditionalDataV3`/`encode_ui_account`, without any `is_known_spl_token_id` gate.

This is inconsistent with the rest of the RPC surface: `get_token_supply` and `get_token_largest_accounts` explicitly reject the mint when `!is_known_spl_token_id(&mint_owner)` right after calling the same helper, as seen at [3](#0-2) , and `get_token_program_id_and_mint` (used by `getTokenAccountsByOwner`/`byDelegate`) performs the identical check at [4](#0-3) . The `jsonParsed` decode path is the odd one out that skips this validation, mirroring the reported `addLender` bug class: an "identity/ownership" field (`want` token vs. lender's actual want; here, the mint's owning program vs. the actual SPL Token program) is used without being cross-checked against the expected value.

An attacker fully controls the contents of an SPL token account they own (owned by the real Token/Token-2022 program, so it passes the outer program-id/filter checks in `get_program_accounts`/`get_filtered_spl_token_accounts_by_*`), and can set its embedded `mint` field to point at *any* other pubkey they also control — e.g., an account owned by an arbitrary unrelated program whose raw bytes happen to unpack into a valid `spl_token_2022_interface::state::Mint` layout (82+ bytes matching the `Mint` packing, decimals byte fully attacker-chosen).

### Impact Explanation
The RPC node will decode and format a UI token amount (`decimals`, `uiAmount`, `uiAmountString`) using untrusted "mint" data that was never confirmed to originate from a real token-mint account owned by a legitimate SPL Token program. This causes wrong/misleading account data to be returned to RPC clients (wallets, explorers, bots) for `getProgramAccounts`, `getTokenAccountsByOwner`, `getTokenAccountsByDelegate`, `getAccountInfo`, and `accountSubscribe` when `encoding: jsonParsed` is used — a decoder misreporting issue. Because `decimals` is attacker-controlled up to `u8::MAX`, `token_amount_to_ui_amount_v3`'s `10_usize.checked_pow(decimals as u32)` will overflow for large values, silently returning `ui_amount: None` while `uiAmountString` becomes empty — producing incorrect/degraded data rather than the accurate balance a client expects, without any indication of an error.

### Likelihood Explanation
Any unprivileged user can trigger this via a single `getProgramAccounts`/`getTokenAccountsByOwner`/`getAccountInfo` call with `jsonParsed` encoding against a token account they create with a spoofed `mint` field, and via `accountSubscribe` (jsonParsed) for a live-notification variant. No special permissions or validator/peer role are required, and it works against any RPC node running with default `jsonParsed` support.

### Recommendation
In `get_mint_owner_and_additional_data` (`rpc/src/parsed_token_accounts.rs`), reject/short-circuit to "no additional data" when `!is_known_spl_token_id(mint_account.owner())`, matching the check already performed in `get_token_supply`, `get_token_largest_accounts`, and `get_token_program_id_and_mint`. This ensures the decimals/UI-amount data used for `jsonParsed` decoding is always sourced from an account that is actually owned by a recognized SPL Token program, analogous to the recommended `addLender` fix of checking that the lender's `want` token matches the strategy's `want` token before trusting it.

### Proof of Concept
1. Create an SPL Token account A owned by `spl_token_2022::id()` (passes the standard Token-Account-state/owner filters used in `get_filtered_spl_token_accounts_by_owner`).
2. Set A's `mint` field to the pubkey of account B, which A's owner (attacker) also creates — B is owned by an unrelated program (e.g., the system program or any attacker program) but its raw byte layout is padded to match `spl_token_2022_interface::state::Mint`'s packed length, with `decimals = 200` (or any arbitrary value) and `is_initialized = true`.
3. Call `getAccountInfo`/`getProgramAccounts` for A with `{"encoding":"jsonParsed"}` (or `accountSubscribe` with jsonParsed).
4. Observe that `get_mint_owner_and_additional_data(bank, &B)` succeeds (data unpacks as `Mint`) and returns `B`'s attacker-controlled decimals without ever checking `B`'s owner program, and the response's parsed `tokenAmount.decimals`/`uiAmount` reflect the attacker-chosen (bogus) value/`None`, rather than an error indicating "not a Token mint" as would occur through `getTokenSupply` for the same pubkey.

Note: I was unable to execute this against a live node in this environment; the analysis is based on static code review of the `is_known_spl_token_id` checks present in sibling functions versus their absence in `get_mint_owner_and_additional_data`'s call sites. Confirming the exact overflow/`None` behavior end-to-end would require running the RPC test harness.

### Citations

**File:** rpc/src/parsed_token_accounts.rs (L23-70)
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

pub fn get_parsed_token_accounts<I>(
    bank: Arc<Bank>,
    keyed_accounts: I,
) -> impl Iterator<Item = RpcKeyedAccount>
where
    I: Iterator<Item = (Pubkey, AccountSharedData)>,
{
    let mut mint_data: HashMap<Pubkey, AccountAdditionalDataV3> = HashMap::new();
    keyed_accounts.filter_map(move |(pubkey, account)| {
        let additional_data = get_token_account_mint(account.data()).and_then(|mint_pubkey| {
            mint_data.get(&mint_pubkey).cloned().or_else(|| {
                let (_, data) = get_mint_owner_and_additional_data(&bank, &mint_pubkey).ok()?;
                let data = AccountAdditionalDataV3 {
                    spl_token_additional_data: Some(data),
                };
                mint_data.insert(mint_pubkey, data);
                Some(data)
            })
        });
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

**File:** rpc/src/rpc.rs (L2082-2087)
```rust
        let (mint_owner, data) = get_mint_owner_and_additional_data(&bank, &mint)?;
        if !is_known_spl_token_id(&mint_owner) {
            return Err(Error::invalid_params(
                "Invalid param: not a Token mint".to_string(),
            ));
        }
```

**File:** rpc/src/rpc.rs (L2712-2718)
```rust
        TokenAccountsFilter::Mint(mint) => {
            let (mint_owner, _) = get_mint_owner_and_additional_data(bank, &mint)?;
            if !is_known_spl_token_id(&mint_owner) {
                return Err(Error::invalid_params(
                    "Invalid param: not a Token mint".to_string(),
                ));
            }
```
