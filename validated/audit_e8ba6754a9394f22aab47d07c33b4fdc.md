### Title
Missing SPL Token program ownership check on the referenced mint account in `get_token_account_balance` allows fabricated decimals/uiAmount - (File: rpc/src/rpc.rs)

### Summary
`JsonRpcRequestProcessor::get_token_account_balance` reads the `mint` field out of an attacker-supplied token account and passes it to `get_mint_owner_and_additional_data`, but then discards the returned mint-account owner and never validates it against `is_known_spl_token_id`. Any account whose bytes merely unpack successfully as `StateWithExtensions::<Mint>` is accepted as the decimals source, even if that account is not owned by a genuine SPL Token/Token-2022 program, letting an attacker fabricate the `decimals`/`uiAmount` returned by `getTokenAccountBalance`.

### Finding Description
The RPC handler is: [1](#0-0) 

It unpacks the attacker's token account, extracts `mint`, and calls `get_mint_owner_and_additional_data(&bank, mint)`, binding its first return value (the mint account's real `owner()`) to `_` — i.e., it is never checked. Compare this to `get_token_supply` and the `TokenAccountsFilter::Mint` path, both of which explicitly call `is_known_spl_token_id(&mint_owner)` after the same helper: [2](#0-1) [3](#0-2) 

The shared helper itself only looks at account *data*, not its owning program: [4](#0-3) 

`get_additional_mint_data` calls `StateWithExtensions::<Mint>::unpack(data)`, which validates only the byte layout (COption tags, `is_initialized`, extension TLVs), not the account's owning program id. Because an attacker can deploy their own on-chain program and use it to create/write an account with arbitrary bytes matching the 82-byte (or extension-padded) `Mint` layout — with any `decimals` value they choose — and then create a separate token account (legitimately owned by `spl_token`/`spl_token_2022`) whose `mint` field points at that attacker-controlled account, `get_token_account_balance` will:
1. Successfully unpack the token account (owner check on the *token account* passes, since it is genuinely owned by a known token id).
2. Fetch the attacker's fake "mint" account and successfully unpack it as `Mint` because the attacker fully controls its bytes.
3. Use the attacker-chosen `decimals` byte to compute `token_amount_to_ui_amount_v3`, fabricating `uiAmount`/`uiAmountString` in the RPC response — without ever verifying the fake mint account is owned by a real token program.

This differs from the literal wording in the prompt (mint pointing to bytes that fail to unpack, which is already correctly rejected via the `map_err(...) => Error::invalid_params("Invalid param: Token mint could not be unpacked")` at rpc/src/parsed_token_accounts.rs:111-114) — that specific case is already handled correctly. The actual exploitable gap is the missing **owner** check on the resolved mint account, which the code performs inconsistently elsewhere but omits here.

### Impact Explanation
This is a parse-fidelity / data-integrity bug: `getTokenAccountBalance` can return a `UiTokenAmount` whose `decimals`/`uiAmount`/`uiAmountString` are derived from an account that is not a genuine, program-validated SPL Mint. Downstream integrators (wallets, exchanges, explorers) that trust this RPC response for display or accounting could show fabricated balances for a token account that in fact belongs to a legitimate token program, misleading users about token quantities. No consensus state or bank data is corrupted — this is response-fidelity misreporting, matching the PARSE_FIDELITY category referenced in the question.

### Likelihood Explanation
Fully attacker-reachable with a single unprivileged `getTokenAccountBalance` call. Preconditions require only permissionless actions available to any user: deploying a program, creating a data account with attacker-chosen bytes shaped like a `Mint`, and creating a token account (owned by `spl_token`/`spl_token_2022`) whose `mint` field references that account. No validator, leader, or staked-node privileges are needed, and the exploit is fully repeatable.

### Recommendation
In `get_token_account_balance` (rpc/src/rpc.rs), use the mint-account owner returned by `get_mint_owner_and_additional_data` and reject the request if it is not a known SPL Token program id, mirroring the check already done in `get_token_supply` and `get_token_program_id_and_mint`:
```rust
let (mint_owner, data) = get_mint_owner_and_additional_data(&bank, mint)?;
if !is_known_spl_token_id(&mint_owner) {
    return Err(Error::invalid_params("Invalid param: not a Token mint".to_string()));
}
```

### Proof of Concept
Integration test to add near the existing `getTokenAccountBalance` tests in `rpc/src/rpc.rs` (around line 8011-8220), following the existing pattern of using `bank.store_account` to simulate attacker-controlled on-chain state:
```rust
#[test]
fn test_get_token_account_balance_fake_mint_owner() {
    let rpc = RpcHandler::start();
    let bank = rpc.working_bank();
    let RpcHandler { io, meta, .. } = rpc;

    // Attacker-controlled "mint" account: valid Mint byte-layout, but NOT owned
    // by spl_token/spl_token_2022 (e.g. owned by attacker's own program).
    let fake_mint_pubkey = solana_pubkey::new_rand();
    let fake_owner = solana_pubkey::new_rand(); // stand-in for attacker's program id
    let mint_base = Mint {
        mint_authority: COption::None,
        supply: 0,
        decimals: 250, // attacker-chosen, fabricated decimals
        is_initialized: true,
        freeze_authority: COption::None,
    };
    let mut mint_data = vec![0; Mint::get_packed_len()];
    Mint::pack(mint_base, &mut mint_data).unwrap();
    let fake_mint_account = AccountSharedData::from(Account {
        lamports: 111,
        data: mint_data,
        owner: fake_owner, // NOT a known SPL token id
        ..Account::default()
    });
    bank.store_account(&fake_mint_pubkey, &fake_mint_account);

    // Genuine token account owned by spl_token, pointing at the fake mint.
    let token_account_pubkey = solana_pubkey::new_rand();
    let account_base = TokenAccount {
        mint: fake_mint_pubkey,
        owner: solana_pubkey::new_rand(),
        delegate: COption::None,
        amount: 420,
        state: TokenAccountState::Initialized,
        is_native: COption::None,
        delegated_amount: 0,
        close_authority: COption::None,
    };
    let mut account_data = vec![0; TokenAccount::get_packed_len()];
    TokenAccount::pack(account_base, &mut account_data).unwrap();
    let token_account = AccountSharedData::from(Account {
        lamports: 111,
        data: account_data,
        owner: spl_token_interface::id(),
        ..Account::default()
    });
    bank.store_account(&token_account_pubkey, &token_account);

    let req = format!(
        r#"{{"jsonrpc":"2.0","id":1,"method":"getTokenAccountBalance","params":["{token_account_pubkey}"]}}"#,
    );
    let res = io.handle_request_sync(&req, meta.clone());
    let result: Value = serde_json::from_str(&res.expect("actual response")).unwrap();

    // EXPECTED (fixed behavior): request should fail because the mint account
    // is not owned by a known SPL Token program.
    assert!(result.get("error").is_some(), "expected invalid_params error for non-token-owned mint");

    // CURRENT (vulnerable) behavior: this instead succeeds and returns
    // balance.decimals == 250, fabricated from the attacker-controlled account.
}
```

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

**File:** rpc/src/rpc.rs (L2707-2719)
```rust
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
