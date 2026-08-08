### Title
`get_token_account_balance` misreports token decimals/amount by decoding a stale/attacker-controlled "mint" account without verifying it is actually owned by a real SPL Token program - ([File: rpc/src/rpc.rs])

### Summary
`RpcClient`/JSON-RPC `getTokenAccountBalance` resolves the `mint` field embedded in a token account's raw bytes and blindly attempts to `StateWithExtensions::<Mint>::unpack` whatever account currently lives at that pubkey, discarding the returned account owner. Unlike the sibling functions `get_token_supply` and `get_token_program_id_and_mint`/`get_token_largest_accounts`, which explicitly validate `is_known_spl_token_id(&mint_owner)`, `get_token_account_balance` never checks that the "mint" account is actually owned by a real token program, so any account whose bytes merely happen to satisfy the `Mint` layout will be accepted as legitimate mint metadata.

### Finding Description
`get_token_account_balance` in `rpc/src/rpc.rs` reads the token account, unpacks it as `TokenAccount`, and extracts the embedded `mint` pubkey field directly from account bytes without any validation that this pubkey currently points to a real mint: [1](#0-0) 

It then calls `get_mint_owner_and_additional_data`, but discards the returned owner via `let (_, data) = ...`: [2](#0-1) 

`get_mint_owner_and_additional_data` fetches whatever account currently exists at that pubkey and passes its raw bytes straight into `get_additional_mint_data`, which only performs a byte-layout `StateWithExtensions::<Mint>::unpack` — it never checks the fetched account's owner program: [3](#0-2) 

By contrast, both other RPC entry points that resolve a mint validate the owner before trusting the decoded data: [4](#0-3) [5](#0-4) 

Exploit flow (all steps are ordinary, unprivileged transactions signed by the attacker):
1. Attacker generates a mint keypair `M`, initializes it as a real SPL Token-2022 mint with the `MintCloseAuthority` extension (legitimate `InitializeMint`/`InitializeMintCloseAuthority`), and creates a real token account `A` (via the token program's `InitializeAccount`, which validates `M` at creation time — this is why the initial creation must go through a real mint).
2. Attacker burns supply of `M` to zero and calls Token-2022's `CloseAccount` on the mint (permitted once supply is zero and the close authority signs), returning `M`'s lamports and deleting the account.
3. Because `M` was created from an attacker-held keypair (not a PDA), the attacker can reuse the same address: fund `M` and have any program of the attacker's choosing (including one deployed by the attacker) create/own a brand-new account at pubkey `M`, writing arbitrary bytes shaped like an initialized 82-byte `Mint` struct with a fabricated `decimals` value.
4. Token account `A` (unmodified, still owned by the real token program, still has stale `mint = M` embedded in its bytes) is queried via `getTokenAccountBalance(A)`.
5. `get_token_account_balance` fetches account `M` (now owned by an arbitrary, non-token program), successfully `unpack`s it as a `Mint` because the check is purely byte-layout based and the owner is discarded, and returns a `UiTokenAmount` with the attacker-chosen `decimals` combined with `A`'s real `amount`.

This is possible because parsing at this call site "trusts the byte offset, not actual mint validity/ownership" exactly as the audit hypothesis states, and because the owner check present in the other two callers of `get_mint_owner_and_additional_data` is absent here.

### Impact Explanation
A single unprivileged `getTokenAccountBalance` JSON-RPC call can return a `UiTokenAmount` whose `decimals` (and consequently `uiAmount`/`uiAmountString`) are entirely attacker-controlled while `amount` reflects the real token account's raw base units. Any downstream consumer (exchange, wallet, indexer) that trusts this RPC response for valuation can display or credit a wildly incorrect balance for that token account, matching the "misreported ... amount, or decimals" invariant violation described in the prompt. This is a decoder/misreporting bug reachable with one low-rate RPC call, not requiring validator/leader/peer control.

### Likelihood Explanation
Feasible and repeatable with only client-side capabilities: creating/initializing a Token-2022 mint with `MintCloseAuthority`, closing it once supply is zero, and reusing the same keypair address to install different account data are all standard, permissionless SPL Token-2022/System Program operations. No special privileges, leaked keys, or validator-side actions are required — only ordinary signed transactions from the attacker's own keys plus one subsequent `getTokenAccountBalance` RPC call.

### Recommendation
In `get_token_account_balance` (rpc/src/rpc.rs), stop discarding the mint account's owner returned by `get_mint_owner_and_additional_data`; require `is_known_spl_token_id(&mint_owner)` (and ideally that it matches the token account's own owning program) before trusting the decoded `SplTokenAdditionalDataV2`, mirroring the checks already present in `get_token_supply`, `get_token_program_id_and_mint`, and `get_token_largest_accounts`. If the owner check fails, return an `Error::invalid_params` instead of silently reporting decoded decimals/amount.

### Proof of Concept
Integration test plan (bank-test style, similar to existing `rpc.rs` token tests):
1. Create mint `M` as Token-2022 with `MintCloseAuthority` extension, `decimals = 6`, mint some supply.
2. Create token account `A` owned by Token-2022, `mint = M`, `amount = 1_000_000`.
3. Call `get_token_account_balance(&A, None)`; assert `decimals == 6`.
4. Burn all supply of `M` to zero; close `M` via `CloseAccount` (attacker-held close authority) so the account is removed from the bank.
5. Re-create an account at the same pubkey `M`, but owned by a different (non-token) program, with data hand-crafted to satisfy `StateWithExtensions::<Mint>::unpack` while setting `decimals = 255` (or any arbitrary value).
6. Call `get_token_account_balance(&A, None)` again.
7. Expected (fixed) behavior: the call returns `Error::invalid_params` because `M`'s owner is not a known SPL token program.
   Current (buggy) behavior: the call succeeds and returns `UiTokenAmount { decimals: 255, amount: "1000000", ... }`, demonstrating attacker-controlled decimals for account `A`'s unchanged real balance.

### Citations

**File:** rpc/src/rpc.rs (L2028-2035)
```rust
        let token_account = StateWithExtensions::<TokenAccount>::unpack(account.data())
            .map_err(|_| Error::invalid_params("Invalid param: not a Token account".to_string()))?;
        let mint = &Pubkey::from_str(&token_account.base.mint.to_string())
            .expect("Token account mint should be convertible to Pubkey");
        let (_, data) = get_mint_owner_and_additional_data(&bank, mint)?;
        let balance = token_amount_to_ui_amount_v3(token_account.base.amount, &data);
        Ok(new_response(&bank, balance))
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

**File:** rpc/src/rpc.rs (L2707-2720)
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
