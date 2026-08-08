### Title
Token decimals/program misreported when a token account's `mint` field points to an unvalidated non-token-owned account - (File: rpc/src/parsed_token_accounts.rs)

### Finding Description
`get_additional_mint_data` in [1](#0-0)  unpacks whatever bytes are stored at the referenced mint pubkey directly as an SPL `Mint` struct, with no check that the account is actually owned by a recognized SPL token program (`spl_token::id()` / `spl_token_2022::id()`). The `mint_pubkey` is taken verbatim from the token account's data via `get_token_account_mint` and then resolved with `bank.get_account`/`get_account_from_overwrites_or_bank`, with no ownership cross-validation.

This missing check appears in three call sites:
- `get_parsed_token_account` (used by `getAccountInfo(jsonParsed)`, simulation results) never inspects `mint_account.owner()` before calling `get_additional_mint_data`: [2](#0-1) 
- `get_parsed_token_accounts` (used by `getProgramAccounts`, `getTokenAccountsByOwner`, `getTokenAccountsByDelegate` with `jsonParsed` encoding) discards the returned owner with `let (_, data) = get_mint_owner_and_additional_data(...)`: [3](#0-2) 
- `get_token_account_balance` (`getTokenAccountBalance`) also discards the returned owner: [4](#0-3) 

In `get_mint_owner_and_additional_data`, the true account owner is fetched (`*mint_account.owner()`) and correctly returned to callers, but only `get_token_program_id_and_mint` (used for `TokenAccountsFilter::Mint`) actually checks `is_known_spl_token_id(&mint_owner)` before use, as seen at [5](#0-4) . The other three call sites above skip this check entirely, so any account whose raw bytes structurally unpack as a valid `Mint` (i.e., correct length/`is_initialized` layout) will have its `decimals` (and interest-bearing/scaled-UI-amount extension config) used for UI amount computation and JSON-parsed rendering — even if that account is not owned by any SPL token program.

Since `StateWithExtensions::<Mint>::unpack` only performs structural byte-layout validation and does not check the runtime `owner` field of the account, this design gap allows the decimals/extension metadata shown for a real token account's balance to be sourced from an account that was never validated as an actual mint owned by the token program the account claims to belong to.

### Impact Explanation
This falls under the "decoder... misreporting" category explicitly permitted by the audit rules. A wallet/exchange/integrator calling `getTokenAccountBalance` or `getAccountInfo(pubkey, jsonParsed)` can receive a `UiTokenAmount`/`UiTokenAccount` with `decimals` (and potentially bogus interest-bearing/scaled-UI-amount scaling) derived from an account that is not a legitimately owned SPL mint, silently corrupting the human-readable balance/amount fields returned by these read-only RPC endpoints without any RPC error being raised.

### Likelihood Explanation
The precondition is simply that some account exists on-chain whose 32-byte `mint` field of a token account points to any account whose data happens to structurally unpack as an SPL `Mint` (82-byte layout with valid `is_initialized`/COption fields), regardless of that account's actual owning program. Per the audit's threat model, "writing on-chain data that is later returned through those APIs" is an accepted unprivileged attacker action, and the RPC call itself is a single, low-rate `getTokenAccountBalance`/`getAccountInfo` request, well within rate limits.

### Recommendation
In `get_mint_owner_and_additional_data`, `get_additional_mint_data`, and all three call sites (`get_parsed_token_account`, `get_parsed_token_accounts`, `get_token_account_balance`), validate `is_known_spl_token_id(mint_account.owner())` before treating the account's data as mint metadata, returning an explicit error (or omitting `additional_data`) when the owner check fails, consistent with the check already performed in `get_token_program_id_and_mint`.

### Proof of Concept
```rust
// rpc/src/parsed_token_accounts.rs (test module) or rpc/src/rpc.rs integration test
#[test]
fn test_misreported_decimals_from_unowned_mint() {
    let bank = ...; // test bank as used elsewhere in rpc.rs tests
    let fake_mint_pubkey = Pubkey::new_unique();

    // Craft a buffer that unpacks as a valid Mint, but owned by an arbitrary
    // (non spl-token) program, e.g. system_program::id().
    let mut mint_data = vec![0; Mint::get_packed_len()];
    let mint_state = Mint {
        mint_authority: COption::None,
        supply: 0,
        decimals: 250, // deliberately corrupted/absurd decimals
        is_initialized: true,
        freeze_authority: COption::None,
    };
    Mint::pack(mint_state, &mut mint_data).unwrap();
    let fake_mint_account = AccountSharedData::from(Account {
        lamports: 111,
        data: mint_data,
        owner: solana_system_interface::program::id(), // NOT owned by spl-token
        ..Account::default()
    });
    bank.store_account(&fake_mint_pubkey, &fake_mint_account);

    // A token account referencing the fake mint (as if created on-chain).
    let token_account_pubkey = Pubkey::new_unique();
    let mut account_data = vec![0; TokenAccount::get_packed_len()];
    let token_account = TokenAccount {
        mint: fake_mint_pubkey,
        owner: Pubkey::new_unique(),
        amount: 1_000_000,
        state: TokenAccountState::Initialized,
        ..TokenAccount::default()
    };
    TokenAccount::pack(token_account, &mut account_data).unwrap();
    let token_account_shared = AccountSharedData::from(Account {
        lamports: 111,
        data: account_data,
        owner: spl_token_interface::id(),
        ..Account::default()
    });
    bank.store_account(&token_account_pubkey, &token_account_shared);

    // Expect: getTokenAccountBalance / getAccountInfo(jsonParsed) should error
    // out because the mint is not owned by a known SPL token program.
    let result = get_token_account_balance(&bank, &token_account_pubkey, None);
    assert!(
        result.is_err(),
        "expected error because mint is not owned by a token program, \
         but decimals=250 from an unvalidated account were used instead"
    );
}
```
Currently this assertion fails (no error is raised, and decimals=250 is silently propagated into the UI amount / JSON-parsed output), confirming the missing owner validation.

### Citations

**File:** rpc/src/parsed_token_accounts.rs (L30-41)
```rust
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

**File:** rpc/src/parsed_token_accounts.rs (L61-70)
```rust
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

**File:** rpc/src/rpc.rs (L2028-2034)
```rust
        let token_account = StateWithExtensions::<TokenAccount>::unpack(account.data())
            .map_err(|_| Error::invalid_params("Invalid param: not a Token account".to_string()))?;
        let mint = &Pubkey::from_str(&token_account.base.mint.to_string())
            .expect("Token account mint should be convertible to Pubkey");
        let (_, data) = get_mint_owner_and_additional_data(&bank, mint)?;
        let balance = token_amount_to_ui_amount_v3(token_account.base.amount, &data);
        Ok(new_response(&bank, balance))
```

**File:** rpc/src/rpc.rs (L2705-2731)
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
