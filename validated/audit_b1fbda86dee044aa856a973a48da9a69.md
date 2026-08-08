## Analysis

The reported class of bug is a **missing type/ownership validation before parsing account data and using it in financial calculations** — `TokenBridge` decodes/uses ERC-721 data as if it must be ERC-20, with no check that the token is actually the expected type before it is relied upon. The closest concrete analog in agave lives in the unprivileged `getTokenAccountBalance` JSON-RPC handler, which parses a *referenced* mint account as an SPL `Mint` without verifying that account is actually owned by a known SPL Token program, unlike its sibling RPC methods which do perform that check.

### Title
`getTokenAccountBalance` computes token balance using an unverified mint account, allowing wrong decimals/`UiTokenAmount` to be returned - (File: `rpc/src/rpc.rs`)

### Summary
`JsonRpcRequestProcessor::get_token_account_balance` fetches the "mint" account referenced by a token account and parses it directly as an SPL `Mint` via `get_mint_owner_and_additional_data`, but discards the returned mint-owner program id without validating it against `is_known_spl_token_id`. Every other RPC handler that consumes `get_mint_owner_and_additional_data` (`get_token_supply` inline, and `get_token_largest_accounts`) performs this ownership check; `get_token_account_balance` does not.

### Finding Description [1](#0-0) 

```
let (_, data) = get_mint_owner_and_additional_data(&bank, mint)?;
```

The mint-owner value is explicitly discarded (`let (_, data)`), whereas `get_token_largest_accounts` uses the same helper and correctly enforces: [2](#0-1) 

The helper itself simply unpacks whatever bytes live at the "mint" pubkey as a `Mint`, with no owner check performed inside it either: [3](#0-2) 

`StateWithExtensions::<Mint>::unpack` only validates the byte layout/TLV structure of the data — it has no concept of which program owns the account. Consequently, decimals/interest-bearing/scaled-UI-amount configuration used to compute the returned `UiTokenAmount` for `getTokenAccountBalance` can come from an account that is not a genuine SPL Token or Token-2022 mint at all, if the pubkey originally referenced as "mint" by a token account is later reassigned to hold unrelated data (e.g., after being closed via Token-2022's `MintCloseAuthority` extension and recreated for another purpose by the address owner).

### Impact Explanation
This causes `getTokenAccountBalance` — an unprivileged, single-call JSON-RPC method — to return an incorrect `UiTokenAmount` (wrong `decimals`, `uiAmount`, `uiAmountString`, and potentially bogus interest-bearing/scaled-UI multipliers) for a still-existing token account, i.e., **wrong account data returned** from a single low-rate RPC query, which is one of the explicitly accepted impact categories.

### Likelihood Explanation
Likelihood is low-to-moderate: it requires the caller to control the keypair of a pubkey that was once a legitimate mint (so they can later reassign/reinitialize that address for another purpose after closing it, e.g., via the Token-2022 `MintCloseAuthority` extension) while a token account still references it as its "mint" field. This is achievable entirely by an unprivileged user through ordinary account lifecycle operations they fully control, without needing validator/operator privileges, but it is a more contrived precondition than a stateless single-call trigger.

### Recommendation
In `get_token_account_balance` (`rpc/src/rpc.rs`), validate the mint-owner value returned by `get_mint_owner_and_additional_data` against `is_known_spl_token_id` (mirroring `get_token_largest_accounts`) before using the decoded decimals/additional data to compute the balance, returning an `invalid_params` error otherwise.

### Proof of Concept
1. As an unprivileged user, create a Token-2022 `Mint` `M` with the `MintCloseAuthority` extension, decimals = 9.
2. Create token account `A` for mint `M`, mint tokens into it.
3. Burn all tokens in `A`'s mint supply to zero, then close `M` via the mint-close-authority instruction (returns `M`'s lamports, resets owner to System Program).
4. Recreate an account at the same address as `M` (the caller holds `M`'s keypair) via `system_instruction::create_account`, assigning it to a different program with data that happens to parse as a `Mint` layout (or simply zeroed default `Mint`-shaped data if attacker controls the writing program).
5. Call `getTokenAccountBalance` on `A`. `get_mint_owner_and_additional_data` unpacks the new data at `M`'s address as a `Mint` without checking its owner is `spl_token_interface::id()`/`spl_token_2022_interface::id()`, and returns a `UiTokenAmount` computed from unrelated/attacker-influenced decimals rather than an error. [1](#0-0) [4](#0-3)

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

**File:** rpc/src/rpc.rs (L2076-2087)
```rust
    pub async fn get_token_largest_accounts(
        &self,
        mint: Pubkey,
        commitment: Option<CommitmentConfig>,
    ) -> Result<RpcResponse<Vec<RpcTokenAccountBalance>>> {
        let bank = self.bank(commitment);
        let (mint_owner, data) = get_mint_owner_and_additional_data(&bank, &mint)?;
        if !is_known_spl_token_id(&mint_owner) {
            return Err(Error::invalid_params(
                "Invalid param: not a Token mint".to_string(),
            ));
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
