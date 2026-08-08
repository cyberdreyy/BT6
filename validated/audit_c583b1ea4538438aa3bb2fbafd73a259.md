## Title
`getTokenAccountBalance`/JSON-parsed token RPC handlers derive token decimals from a `mint` address without validating it is owned by a known SPL Token program, allowing misreported balances - (File: `rpc/src/rpc.rs`, `rpc/src/parsed_token_accounts.rs`)

### Summary
Several unprivileged JSON-RPC handlers that decode SPL-token accounts fetch the referenced mint's decimals/extension config via `get_mint_owner_and_additional_data`, but then discard the returned program-owner and never verify that the mint account is actually owned by a known SPL Token program before using its parsed data to compute the returned `UiTokenAmount`. This mirrors the reported Vader `Pools.mintSynth` class of bug, where a value derived from an unvalidated "base"/reference account is trusted for a financial-style calculation, corrupting the resulting output.

### Finding Description
`get_token_account_balance` unpacks the caller-supplied token account (after confirming *that* account's owner is a known SPL token program), then reads the account's embedded `mint` field and calls `get_mint_owner_and_additional_data`, discarding the returned owner: [1](#0-0) 

Compare this to `get_token_supply`, which explicitly checks `is_known_spl_token_id(mint_account.owner())` before trusting the mint data: [2](#0-1) 

The shared helper itself performs no ownership validation — it will happily unpack *any* account's bytes as a `Mint` if they happen to match the packed layout, regardless of which program owns that account: [3](#0-2) 

The same unchecked pattern is used by `get_parsed_token_account` and `get_parsed_token_accounts`, which back `jsonParsed`-encoded results for `getAccountInfo`, `getProgramAccounts`, and `getTokenAccountsByOwner`: [4](#0-3) 

Because Solana's runtime resets an account's owner to the System Program once its lamports reach zero, a mint address can later be reclaimed and rewritten by an attacker-controlled program with fabricated bytes that still satisfy the `Mint`-unpack layout (correct `COption` discriminants, `is_initialized = 1`, and an arbitrary `decimals` byte) while any pre-existing token account that still references that stale mint pubkey remains untouched. Since none of `get_token_account_balance`, `get_parsed_token_account`, or `get_parsed_token_accounts` re-validate the mint's current owner, an attacker fully controls the `decimals`/`SplTokenAdditionalDataV2` used to compute the `UiTokenAmount` and `uiAmount`/`uiAmountString` fields returned to any RPC caller for that token account.

### Impact Explanation
This causes wrong account data to be returned by unprivileged, single-request JSON-RPC calls (`getTokenAccountBalance`, `getAccountInfo` with `jsonParsed`, `getProgramAccounts`/`getTokenAccountsByOwner` with `jsonParsed`), i.e., a decoder-misreporting condition: the human-readable UI balance (`uiAmount`) for a token account can be arbitrarily skewed by an attacker who controls only an unrelated account address reuse, without needing any special privilege and with a single call. This falls squarely in the accepted impact category "wrong-slot/fork/account data returned or decoder panic and misreporting."

### Likelihood Explanation
Reproducing this requires only standard, permissionless operations: creating and later closing/reclaiming an account at a specific address (achievable by any user with a custom program or via a closable mint, e.g., Token-2022 mint-close extension) and having a stale token account still reference that address as its `mint`. No validator or operator privileges are needed, and the exploit is triggered by a normal read-only RPC query.

### Recommendation
In `get_mint_owner_and_additional_data`'s call sites (`get_token_account_balance` in `rpc/src/rpc.rs`, and `get_parsed_token_account`/`get_parsed_token_accounts` in `rpc/src/parsed_token_accounts.rs`), verify that the returned mint-account owner is a known SPL token program id (using the same `is_known_spl_token_id` check already used in `get_token_supply` and `get_token_program_id_and_mint`) before using the mint's decoded decimals/extension data to compute `UiTokenAmount`, or fall back to raw/base64 encoding when the mint owner is not a recognized token program.

### Proof of Concept
1. Create a real SPL token mint `M` (owned by `spl_token_interface::id()`) and a token account `A` (owned by the same program) whose `mint` field is `M`, with some `amount`.
2. Reduce `M`'s lamports to zero within a single transaction (e.g., via a Token-2022 mint close-authority extension, or any mechanism that empties the account), causing the runtime to reset `M`'s owner to the System Program.
3. Using an attacker-controlled program, `Assign`/allocate account `M` again and write 82 bytes matching the `Mint` packed layout (`is_initialized = 1`, valid `COption` tags, `decimals = 253`), with the account now owned by the attacker's arbitrary program (not any `is_known_spl_token_id`).
4. Call the JSON-RPC method `getTokenAccountBalance` with pubkey `A` (see handler at [1](#0-0) ). The response's `decimals`/`uiAmount` will be computed from the attacker-controlled 82 bytes at `M`, since no owner check occurs, producing an incorrect UI balance despite `A`'s underlying raw `amount` field being unchanged.

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
