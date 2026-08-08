### Title
`getTokenAccountBalance` / JSON-parsed token decoding trusts an unvalidated `mint` field, returning wrong decimals/UI amount - (File: `rpc/src/parsed_token_accounts.rs`, `rpc/src/rpc.rs`)

### Summary
Several unprivileged JSON-RPC code paths that decode SPL token accounts pull the `mint` pubkey directly out of the token-account bytes and use it to fetch "additional mint data" (decimals, interest-bearing/scaled-UI configs) — without ever checking that the account at that `mint` address is actually owned by a known SPL token program. This is the same bug class as the BakerFi report: one field (`_asset`/approval target) is trusted to be consistent with another (`loanToken`/actual transfer target) without validation. Here, the token account's self-reported `mint` field is trusted to be a legitimate Mint account without verifying its owner, so an attacker can point it at an arbitrary, attacker-controlled account and make the RPC layer misreport balances/decimals for that token account.

### Finding Description
`get_mint_owner_and_additional_data` fetches whatever account lives at the given `mint` pubkey and unpacks it directly as an SPL `Mint`, with no ownership check: [1](#0-0) 

`get_additional_mint_data`, which it calls, also performs no owner validation, only that the bytes happen to `unpack` as a `Mint`: [2](#0-1) 

This function is reached from `get_parsed_token_account` and `get_parsed_token_accounts`, which take the `mint` field straight out of arbitrary token-account bytes via `get_token_account_mint` and use it as a bank-lookup key with no owner check, then feed the result into `encode_ui_account` for `jsonParsed` encoding (used by `getAccountInfo`, `getMultipleAccounts`, `getProgramAccounts`, and `getTokenAccountsByOwner`): [3](#0-2) 

The same missing check exists in `get_token_account_balance` (backing `getTokenAccountBalance`): it validates that the *token account itself* is owned by a known SPL token program, but then extracts `mint` from the account body and passes it straight to `get_mint_owner_and_additional_data` with no validation that the mint account is owned by a token program: [4](#0-3) 

Notably, the sibling function `get_token_supply` (for `getTokenSupply`) *does* perform this check (`is_known_spl_token_id(mint_account.owner())`) before unpacking the mint, showing the validation is known to be necessary but is inconsistently applied elsewhere: [5](#0-4) 

Since `Account::unpack`/`StateWithExtensions::<Account>::unpack` only validates the SPL-Token account layout (mint, owner, amount, delegate, state, etc.), any user can create and own (via System Program or any program) a data blob laid out exactly like an SPL `Mint` (82 bytes: `COption<Pubkey>` authority, `u64` supply, `u8` decimals, `bool is_initialized=true`, `COption<Pubkey>` freeze authority) at an address of their choosing, and set the `mint` field of a real SPL token account to point at it. Because none of these paths check `mint_account.owner()` against `is_known_spl_token_id`, the RPC will happily use the attacker-chosen `decimals` (and interest-bearing/scaled-UI extension data, though extensions are only parsed via `StateWithExtensions` for genuine token-2022 mints, so a forged plain account would simply supply `decimals`) when computing `ui_amount` for that token account.

### Impact Explanation
This causes JSON-RPC methods (`getTokenAccountBalance`, and `jsonParsed`-encoded `getAccountInfo`/`getMultipleAccounts`/`getProgramAccounts`/`getTokenAccountsByOwner`) to return incorrect `decimals` and `uiAmount`/`uiAmountString` for a token account — i.e., wrong account data returned from a single, unprivileged read query. This is a decoder-misreporting bug reachable by any caller with a single RPC call, matching the "wrong-slot/fork/account data returned" acceptance criterion. It does not require a privileged role, mocked environment, or multiple calls — a user only needs to create one SPL token account whose `mint` field points at a forged "mint-shaped" account they control.

### Likelihood Explanation
High likelihood of triggering: creating an SPL token account with an arbitrary `mint` field and a forged 82-byte "mint" account at that address requires only normal, unprivileged account creation (e.g., via System Program) — no special permissions, no consensus interaction, and no multi-client coordination. It is a single RPC call away from misreporting once such accounts exist on-chain.

### Recommendation
In `get_mint_owner_and_additional_data` (and equivalently in `get_token_account_balance`), verify that the account fetched at the token account's `mint` field is owned by a known SPL token program (`is_known_spl_token_id`) before parsing it as a `Mint`, mirroring the check already present in `get_token_supply`. Reject or fall back to raw/base64 encoding when the mint account's owner is not a recognized token program.

### Proof of Concept
1. Create account `FakeMint` (any owner, e.g. System Program) whose data is exactly 82 bytes laid out as an SPL `Mint`: `mint_authority: COption::None`, `supply: <anything>`, `decimals: 255` (or any misleading value), `is_initialized: true`, `freeze_authority: COption::None`.
2. Create a genuine SPL Token account `EvilTokenAccount` (owned by `spl_token_interface::id()`), with `mint = FakeMint`, `owner = <attacker>`, `amount = 1_000_000`.
3. Call `getTokenAccountBalance` for `EvilTokenAccount` (`rpc/src/rpc.rs::get_token_account_balance`, `rpc/src/rpc.rs:2013-2035`): the RPC unpacks `EvilTokenAccount`, reads `mint = FakeMint`, calls `get_mint_owner_and_additional_data(bank, FakeMint)`, which fetches `FakeMint`'s account (no owner check) and unpacks it as a `Mint`, returning `decimals: 255`. The reported `uiAmount`/`decimals` is now attacker-controlled and inconsistent with the actual token's real mint/decimals, even though `FakeMint` is not a real SPL mint account.
4. Equivalently, call `getProgramAccounts`/`getTokenAccountsByOwner` with `encoding: "jsonParsed"` — `get_parsed_token_accounts` (`rpc/src/parsed_token_accounts.rs:52-88`) performs the identical unchecked lookup and will render the same forged decimals in the parsed JSON output.

### Citations

**File:** rpc/src/parsed_token_accounts.rs (L23-88)
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
