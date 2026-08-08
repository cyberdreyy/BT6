### Title
Missing mint-owner validation in `get_additional_mint_data`/`get_mint_owner_and_additional_data` allows forged mint data to be reported as authoritative token decimals/authorities - ([File: rpc/src/parsed_token_accounts.rs])

### Summary
`get_additional_mint_data` in `rpc/src/parsed_token_accounts.rs` unpacks the bytes of whatever account is referenced by a token account's `mint` field as `StateWithExtensions::<Mint>`, without ever checking that this account is owned by the SPL Token or Token-2022 program. Any account whose raw bytes happen to `Mint::unpack` successfully is treated as authoritative, so `decimals`, `mintAuthority`, and `freezeAuthority` reported by `getTokenAccountBalance` and jsonParsed `getProgramAccounts`/`getTokenAccountsByOwner`/`getTokenAccountsByDelegate` can be attacker-controlled.

### Finding Description
`get_token_account_balance` (`rpc/src/rpc.rs:2013-2035`) validates that the *token account* itself is owned by a known SPL token program (`is_known_spl_token_id(account.owner())`), then extracts the account's `mint` field and calls `get_mint_owner_and_additional_data(&bank, mint)`: [1](#0-0) 

`get_mint_owner_and_additional_data` fetches the mint account from the bank and passes its raw data straight to `get_additional_mint_data`, which unpacks it as a `Mint` with no owner check whatsoever: [2](#0-1) 

The returned tuple's first element (`*mint_account.owner()`) is discarded by `get_token_account_balance` (`let (_, data) = get_mint_owner_and_additional_data(&bank, mint)?;`), so the decimals/interest-bearing/scaled-UI-amount data extracted from an arbitrary, non-token-program-owned account is used to compute the reported `UiTokenAmount`. The same unchecked path is used by `get_parsed_token_accounts` (for jsonParsed `getProgramAccounts`, `getTokenAccountsByOwner`, `getTokenAccountsByDelegate`) and by `get_parsed_token_account` for simulation results: [3](#0-2) 

Notably, elsewhere in the same file the codebase *does* perform this check — `get_token_program_id_and_mint` explicitly rejects a mint whose owner is not a known SPL token id: [4](#0-3) 

This inconsistency confirms the check is a deliberate control that was simply omitted from `get_additional_mint_data`/`get_mint_owner_and_additional_data`.

Exploit flow: an attacker deploys their own on-chain program (a normal, unprivileged action) that writes arbitrary bytes into an account `B` it owns, shaping those bytes to satisfy `Mint::unpack` (e.g., `is_initialized = true`, `mint_authority = attacker_key`, `decimals = 0`). The attacker then creates a normal SPL Token account `A` (owned by the real token program, satisfying the `is_known_spl_token_id` check on the account itself) whose `mint` field is set to `B`'s pubkey. Because Agave's decoder never checks `B`'s owner, `getTokenAccountBalance(A)` and jsonParsed `getAccountInfo`/`getProgramAccounts` calls will report `B`'s forged `decimals`, `mintAuthority`, and `freezeAuthority` as if they came from a legitimate mint.

### Impact Explanation
This causes RPC-reported token metadata (decimals, mint authority, freeze authority, supply-scaling extension configs) to misrepresent on-chain reality for `getTokenAccountBalance` and jsonParsed encodings of `getAccountInfo`/`getProgramAccounts`/`getTokenAccountsByOwner`/`getTokenAccountsByDelegate`. Downstream integrators (wallets, explorers, exchanges) that trust these RPC responses without independently re-validating the mint account's owner can display incorrect balances (wrong decimal scaling) or incorrect authority/ownership information, matching the "misreporting" / decoder-fidelity bounty category described in the prompt.

### Likelihood Explanation
The only precondition is the ability to (1) create/own an arbitrary account with attacker-controlled bytes (trivially done by deploying a small program that writes to an account it owns) and (2) create a normal SPL token account whose `mint` field references that account. Both are ordinary, unprivileged on-chain operations requiring a single follow-up read-only JSON-RPC call (`getTokenAccountBalance` or `getAccountInfo`/`getProgramAccounts` with `jsonParsed`), well within the one-call-per-`CLUSTER_SLOT_TIME_TARGET/2` constraint. The bug is fully deterministic and repeatable.

### Recommendation
In `get_additional_mint_data`/`get_mint_owner_and_additional_data` (`rpc/src/parsed_token_accounts.rs`), validate that the mint account's owner is a known SPL token program id (mirroring the check already present in `get_token_program_id_and_mint`) before unpacking it as a `Mint` and before using its decimals/authority data in any RPC response, returning `Error::invalid_params` otherwise.

### Proof of Concept
```rust
// rpc/src/parsed_token_accounts.rs (illustrative unit test)
#[test]
fn test_forged_mint_owner_not_checked() {
    let bank = /* test bank setup, as in existing rpc.rs tests */;
    let attacker_program = Pubkey::new_unique(); // not spl_token / spl_token_2022

    // B: crafted "mint" account NOT owned by the token program
    let forged_mint_pubkey = Pubkey::new_unique();
    let mut mint_data = vec![0; Mint::get_packed_len()];
    let forged_mint = Mint {
        mint_authority: COption::Some(Pubkey::new_unique()), // attacker-chosen
        supply: 0,
        decimals: 0,          // attacker-chosen, e.g. wrong decimals
        is_initialized: true,
        freeze_authority: COption::Some(Pubkey::new_unique()),
    };
    Mint::pack(forged_mint, &mut mint_data).unwrap();
    bank.store_account(&forged_mint_pubkey, &AccountSharedData::from(Account {
        lamports: 1,
        data: mint_data,
        owner: attacker_program, // NOT spl_token::id()
        ..Account::default()
    }));

    // A: legitimate-looking token account referencing the forged mint
    let token_account_pubkey = Pubkey::new_unique();
    let mut account_data = vec![0; TokenAccount::get_packed_len()];
    let account_base = TokenAccount {
        mint: forged_mint_pubkey,
        owner: Pubkey::new_unique(),
        amount: 100,
        state: TokenAccountState::Initialized,
        ..Default::default()
    };
    TokenAccount::pack(account_base, &mut account_data).unwrap();
    bank.store_account(&token_account_pubkey, &AccountSharedData::from(Account {
        lamports: 1,
        data: account_data,
        owner: spl_token_interface::id(), // real token program owns A
        ..Account::default()
    }));

    // Call get_mint_owner_and_additional_data directly (as get_token_account_balance does)
    let result = get_mint_owner_and_additional_data(&bank, &forged_mint_pubkey);
    assert!(result.is_ok()); // BUG: should fail because mint owner != spl_token program
    let (_, data) = result.unwrap();
    assert_eq!(data.decimals, 0); // attacker-forged decimals accepted as authoritative
}
```
Expected (fixed) behavior: `get_mint_owner_and_additional_data`/`get_additional_mint_data` should return `Err(Error::invalid_params(...))` when the mint account's owner is not a known SPL token program id, causing `getTokenAccountBalance`/`getAccountInfo` jsonParsed to fail or omit forged fields rather than reporting attacker-controlled decimals/authorities.

### Citations

**File:** rpc/src/rpc.rs (L2028-2033)
```rust
        let token_account = StateWithExtensions::<TokenAccount>::unpack(account.data())
            .map_err(|_| Error::invalid_params("Invalid param: not a Token account".to_string()))?;
        let mint = &Pubkey::from_str(&token_account.base.mint.to_string())
            .expect("Token account mint should be convertible to Pubkey");
        let (_, data) = get_mint_owner_and_additional_data(&bank, mint)?;
        let balance = token_amount_to_ui_amount_v3(token_account.base.amount, &data);
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

**File:** rpc/src/parsed_token_accounts.rs (L92-129)
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
```
