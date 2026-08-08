## Analysis

The report's bug class is: a function accepts a user/attacker-influenced token/address reference (`outputToken`) without verifying it belongs to the expected, validated set (the basket's known tokens), letting unrelated assets be scooped through a legitimate-looking operation.

The closest reachable analog in this agave codebase is in the JSON-RPC handler `JsonRpcRequestProcessor::get_token_account_balance` [1](#0-0) . It validates that the *token account itself* is owned by a known SPL token program via `is_known_spl_token_id(account.owner())` [2](#0-1) , then extracts the `mint` pubkey embedded in that account's data and calls `get_mint_owner_and_additional_data(&bank, mint)` to fetch decimals, but it discards the returned mint-owner (`let (_, data) = ...`) and never checks that the mint account is actually owned by a recognized SPL token program [3](#0-2) . The helper itself does the raw lookup: [4](#0-3)  and unpacks whatever bytes are found as a `Mint` without checking `mint_account.owner()` against `is_known_spl_token_id` [5](#0-4) .

By contrast, the sibling function `get_token_program_id_and_mint`, used by `getTokenAccountsByOwner`/`getTokenAccountsByDelegate`, explicitly performs this check after the same lookup: [6](#0-5) . This shows the validation is known-necessary elsewhere but is missing in `get_token_account_balance` and in the JSON-parsed encoding path (`get_parsed_token_account`/`get_parsed_token_accounts`) [7](#0-6) .

Because Solana pubkeys can be "recycled" (an account whose lamports are drained to zero is purged; the same keypair can later be reused for an entirely unrelated account), a token creator who closes their mint (e.g., via the Token-2022 `MintCloseAuthority` extension) and later reuses that exact pubkey for arbitrary data can cause any *other* user's still-existing token account referencing that old mint pubkey to have its `getTokenAccountBalance` (and jsonParsed `getTokenAccountsByOwner`/`getProgramAccounts`) results computed from attacker-controlled bytes — wrong decimals/ui_amount reported for a real user's account, from a single unprivileged RPC call, with no re-verification that the mint is actually owned by a legitimate SPL token program.

### Title
Missing SPL-token-program ownership check on mint lookup in `getTokenAccountBalance`/JSON-parsed token encoding leads to misreported balances - (File: rpc/src/rpc.rs, rpc/src/parsed_token_accounts.rs)

### Summary
`get_token_account_balance` and the jsonParsed token-account encoding path (`get_parsed_token_account`/`get_parsed_token_accounts`) resolve a token account's `mint` field to an on-chain account and use its raw data to compute decimals/UI amount, without verifying that account is actually owned by a recognized SPL token program — unlike the equivalent check performed in `get_token_program_id_and_mint`.

### Finding Description
`get_token_account_balance` validates only that the *queried account* is SPL-token-owned, then reads the embedded `mint` pubkey and calls `get_mint_owner_and_additional_data`, discarding the returned owner with `let (_, data) = ...` [8](#0-7) . `get_mint_owner_and_additional_data` unconditionally attempts `StateWithExtensions::<Mint>::unpack` on whatever account currently sits at that pubkey and returns its decimals data if it parses, with no ownership check [4](#0-3) . The jsonParsed encoding helpers (`get_parsed_token_account`, `get_parsed_token_accounts`) follow the same pattern [9](#0-8) [10](#0-9) . This mirrors the report's bug class: a downstream operation trusts a caller/state-controlled reference (`mint`) to select a data source without re-validating that the referenced entity is a member of the expected/trusted set (a genuine SPL token program mint), analogous to `settleAuction()` trusting `outputTokens` without checking basket membership.

### Impact Explanation
On Solana, an account whose lamports are drained to zero is deallocated, and its pubkey can later be reinitialized as a completely unrelated account (this exact "stale/reused account" hazard is separately acknowledged in `get_filtered_spl_token_accounts_by_owner`/`by_mint`, which added redundant filters for it: [11](#0-10) ). A mint creator who closes their mint (e.g., using Token-2022 `MintCloseAuthority`) and later reuses the same pubkey for arbitrary data can cause `getTokenAccountBalance`/jsonParsed queries on any pre-existing (unrelated user's) token account that still references the old mint pubkey to report incorrect decimals/`ui_amount`, without needing that data source to be a legitimate SPL token program account. This is wrong account data returned from a single unprivileged RPC call.

### Likelihood Explanation
Requires the attacker to have originally created a closeable mint (own keypair) with at least one other party having opened an account against it, then closing and reusing the pubkey — a multi-step but attacker-controlled, non-privileged sequence achievable purely through normal transactions; no validator/operator role or crafted snapshot needed.

### Recommendation
In `get_token_account_balance` (and in `get_parsed_token_account`/`get_parsed_token_accounts`/`get_mint_owner_and_additional_data`), verify that the resolved mint account's owner satisfies `is_known_spl_token_id` (and ideally matches the querying token account's own owner/program) before using its data to compute decimals or attach `AccountAdditionalDataV3`, mirroring the check already present in `get_token_program_id_and_mint`.

### Proof of Concept
1. Create Mint `M` with `MintCloseAuthority` (Token-2022), decimals = 9.
2. User creates Token Account `A` for mint `M` (balance e.g. 1000).
3. Mint-authority burns/ensures supply is 0, then closes `M` via `CloseAccount`, draining its lamports to 0; the account is purged.
4. Mint-authority (same keypair) creates a new, unrelated account at pubkey `M` (e.g., another Mint with decimals = 0, or a crafted account owned by the same token program) using `CreateAccount`.
5. Call `getTokenAccountBalance` for `A`: the RPC reads `A`'s stored `mint = M`, fetches whatever is now at `M`, and reports a UI amount/decimals derived from the new (attacker-controlled) account instead of erroring or validating provenance, even though `A` still holds its original raw `amount`.

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

**File:** rpc/src/rpc.rs (L2319-2331)
```rust
        // The by-owner accounts index checks for Token Account state and Owner address on
        // inclusion. However, due to the current AccountsDb implementation, an account may remain
        // in storage as a zero-lamport AccountSharedData::Default() after being wiped and reinitialized in
        // later updates. We include the redundant filters here to avoid returning these accounts.
        //
        // Filter on Token Account state
        filters.push(RpcFilterType::TokenAccountState);
        // Filter on Owner address
        filters.push(RpcFilterType::Memcmp(Memcmp::new_raw_bytes(
            SPL_TOKEN_ACCOUNT_OWNER_OFFSET,
            owner_key.to_bytes().into(),
        )));

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
