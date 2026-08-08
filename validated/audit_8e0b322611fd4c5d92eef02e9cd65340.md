### Title
Hard-coded native-mint address short-circuits mint decoding, returning stale/wrong owner and decimals for on-chain token balances - ([File: rpc/src/parsed_token_accounts.rs])

### Summary
`get_mint_owner_and_additional_data` in `rpc/src/parsed_token_accounts.rs` special-cases the well-known wrapped-SOL mint address by comparing it against the compile-time constant `spl_token_interface::native_mint::id()` and, on a match, returns a hard-coded owner (`spl_token_interface::id()`) and hard-coded decimals (`spl_token_interface::native_mint::DECIMALS`) without ever reading the actual account stored in the current `Bank`. [1](#0-0) 

### Finding Description
This mirrors the reported bug class: a single compile-time constant address is assumed to always carry fixed semantics (owner/program + decimals), and every code path that resolves that address skips the "ask the source of truth" step. In the WETH report, the source of truth is the actual contract deployed at the address on the current chain; here, the source of truth is the account actually stored in the current `Bank` for that pubkey.

`get_mint_owner_and_additional_data` is reached from unprivileged, user-controlled JSON-RPC input:
- `get_token_account_balance` (`getTokenAccountBalance`) calls it directly with the mint pubkey taken from an arbitrary token account supplied by the RPC caller. [2](#0-1) 
- It is also used from the jsonParsed-account-encoding path (`get_parsed_token_accounts`), which backs `getProgramAccounts`/`getTokenAccountsByOwner`/`getTokenAccountsByDelegate` with jsonParsed encoding. [3](#0-2) 

Every other mint address is resolved correctly by loading the actual account from the bank and decoding it with `get_additional_mint_data`, which reads the real `decimals`, `interest_bearing_config`, and `scaled_ui_amount_config` extension fields. [4](#0-3) 

But for the specific address `spl_token_interface::native_mint::id()`, the function never calls `bank.get_account`; it fabricates the result. The codebase itself demonstrates that the account actually backing a mint address is not immutable across the life of a cluster/bank — `runtime/src/bank/tests.rs::test_reconfigure_token2_native_mint` exercises a scenario where the native-mint account's owning program and packed format can be reconfigured at the account level. [5](#0-4) 

Because `get_mint_owner_and_additional_data` never inspects the live account, any of the following would silently be ignored by this fast path even though every other mint is handled account-accurately:
- If a mint extension (`ScaledUiAmountConfig`, `InterestBearingConfig`) were ever attached to this well-known address on a given bank, it would be dropped — decimals/owed-amount math would use the hard-coded `DECIMALS` value and no extension config, instead of the real packed mint state.
- If the token program owning that mint account differs from `spl_token_interface::id()` on some bank state (e.g. reconfigured to `token_2022`), the reported `owner`/program id used for decimal/extension resolution would still be forced to the legacy SPL Token program.

This is a decoder/account-resolution correctness bug: the RPC layer returns fabricated (potentially wrong) account-derived data instead of the bank's actual state, for a query path reachable by any unprivileged RPC client.

### Impact Explanation
`getTokenAccountBalance` and jsonParsed encodings of `getProgramAccounts`/`getTokenAccountsByOwner`/`getTokenAccountsByDelegate` can report an owner/program-id and decimals/UI-amount for the well-known mint address that don't reflect what is actually stored on the bank for that account, i.e. wrong-account-data returned from a query. This falls within the accepted validator-analog impact class ("wrong-slot/fork/account data returned" / "decoder panic and misreporting") because the mismatch is between reported RPC data and true bank state, and it affects an unprivileged, single-call JSON-RPC decoding path.

### Likelihood Explanation
Likelihood is constrained: today the hard-coded owner/decimals happen to match production genesis configuration for that address, so under normal mainnet/testnet/devnet conditions with the current, unmodified spl-token native mint this code path returns the same result as reading the account. The divergence becomes observable only if the account backing that specific address is ever configured differently than the hard-coded assumption (e.g. via test/private genesis setups or mint reconfiguration flows that the codebase itself already exercises in `test_reconfigure_token2_native_mint`), making this a latent correctness bug rather than an actively-triggerable one on unmodified public clusters today.

### Recommendation
Remove the special case in `get_mint_owner_and_additional_data` and always resolve the mint via `bank.get_account(mint)` + `get_additional_mint_data`, exactly like every other mint address, so decimals/extension configuration always reflect the actual on-chain state rather than a compiled-in assumption. If a fast path for the well-known native mint is desired for performance, it should first verify (e.g., via `bank.get_account`) that the on-chain owner/decimals actually match the assumed constants before short-circuiting, and fall back to the general path otherwise.

### Proof of Concept
Not independently reproduced against a live validator in this pass — this analysis is based on static code review of `get_mint_owner_and_additional_data` and its call sites plus the existing `test_reconfigure_token2_native_mint` test demonstrating that the native-mint account's backing state is not immutable. Constructing a concrete divergent-genesis or reconfiguration scenario to trigger a live mismatch would require running the validator/test harness, which was not performed here.

### Citations

**File:** rpc/src/parsed_token_accounts.rs (L52-88)
```rust
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

        let maybe_encoded_account = encode_ui_account(
            &pubkey,
            &account,
            UiAccountEncoding::JsonParsed,
            additional_data,
            None,
        );
        if let UiAccountData::Json(_) = &maybe_encoded_account.data {
            Some(RpcKeyedAccount {
                pubkey: pubkey.to_string(),
                account: maybe_encoded_account,
            })
        } else {
            None
        }
    })
}
```

**File:** rpc/src/parsed_token_accounts.rs (L90-108)
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

**File:** runtime/src/bank/tests.rs (L6271-6282)
```rust
#[test]
fn test_reconfigure_token2_native_mint() {
    agave_logger::setup();

    let genesis_config =
        create_genesis_config_with_leader(5, &solana_pubkey::new_rand(), 0).genesis_config;
    let bank = Arc::new(Bank::new_for_tests(&genesis_config));
    assert_eq!(bank.get_balance(&token::native_mint::id()), 1000000000);
    let native_mint_account = bank.get_account(&token::native_mint::id()).unwrap();
    assert_eq!(native_mint_account.data().len(), 82);
    assert_eq!(native_mint_account.owner(), &token::id());
}
```
