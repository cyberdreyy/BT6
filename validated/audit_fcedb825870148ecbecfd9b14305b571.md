This confirms the pattern: `get_token_account_balance` (rpc/src/rpc.rs:2013-2035) calls `get_mint_owner_and_additional_data(&bank, mint)` and discards the returned owner with `let (_, data) = ...`, unlike the sibling function `get_token_largest_accounts` (rpc/src/rpc.rs:2076-2087), which explicitly checks `is_known_spl_token_id(&mint_owner)` before trusting the decimals. `spl-token-2022`'s `MintCloseAuthority` extension (confirmed present in `account-decoder/src/parse_token_extension.rs`) allows a mint with zero supply to be closed, freeing its address for reuse by the original keypair holder under any other program, with arbitrary 82-byte data crafted to still successfully unpack as a `Mint` struct.

### Title
`getTokenAccountBalance` RPC trusts a self-reported mint address without verifying its owning program, allowing wrong decimals/balance to be returned - (File: rpc/src/rpc.rs)

### Summary
The `getTokenAccountBalance` RPC method reads the `mint` field embedded in a caller-supplied SPL Token account and looks up that mint address in the bank to obtain decimals for formatting the UI balance, but never verifies that the account found at that address is still owned by a genuine SPL Token program. This mirrors the reported bug class where a trusted "address" field is used to authorize/derive sensitive data without the caller re-validating that the address still points to what it is supposed to, allowing wrong data to be returned to any unprivileged RPC caller.

### Finding Description
`JsonRpcRequestProcessor::get_token_account_balance` unpacks the caller's token account and extracts its embedded `mint` field, then calls `get_mint_owner_and_additional_data(&bank, mint)` to fetch decimals/interest-bearing config for formatting the balance: [1](#0-0) 

Critically, the returned mint-account owner is discarded with `let (_, data) = ...`, so the code never confirms that the account currently residing at the `mint` address is actually owned by a known SPL Token program: [2](#0-1) 

Contrast this with the sibling function `get_token_largest_accounts`, which performs the same lookup via `get_mint_owner_and_additional_data` but explicitly checks the returned owner with `is_known_spl_token_id(&mint_owner)` before trusting the decimals data: [3](#0-2) 

`get_mint_owner_and_additional_data` itself performs no ownership validation — it simply fetches whatever account exists at the given address and attempts to unpack it as a `Mint`, returning the owner and decimals if unpacking succeeds: [4](#0-3) 

Because `spl-token-2022` mints can be closed once supply reaches zero if they carry the `MintCloseAuthority` extension, the original account address can become vacant and later be recreated at the same pubkey (using the same keypair) as an account owned by an arbitrary program, with data crafted to still unpack cleanly as a valid `Mint` (82-byte layout: `mint_authority`(36) + `supply`(8) + `decimals`(1) + `is_initialized`(1) + `freeze_authority`(36)), e.g. with a manipulated `decimals` value. Existing SPL token accounts that still reference the old mint address (created before the mint was closed) remain valid Token-program-owned accounts, so `is_known_spl_token_id(account.owner())` at line 2023 still passes, but the decimals/interest-bearing data fetched from the now-repurposed mint address is attacker-controlled and no longer validated as coming from a real token program.

### Impact Explanation
Any unprivileged caller of the public `getTokenAccountBalance` JSON-RPC method can be shown an incorrect `UiTokenAmount` (wrong `decimals`, `uiAmount`, `uiAmountString`) for a legitimate token account, because the decimals value is sourced from an unvalidated, attacker-controllable mint address. This falls under "wrong account data returned" via a single RPC query, without requiring any validator/peer/operator role.

### Likelihood Explanation
Triggering this requires only that a mint address later be closable and reused, a normal capability of `spl-token-2022` mints with the `MintCloseAuthority` extension, combined with a subsequent call to `getTokenAccountBalance` referencing a token account that still points at the closed mint address. No special privileges, multiple correlated RPC calls, or peer/validator control are required.

### Recommendation
In `get_token_account_balance`, do not discard the mint-account owner returned by `get_mint_owner_and_additional_data`; validate it with `is_known_spl_token_id` (as already done in `get_token_supply` and `get_token_largest_accounts`) before using its decimals to format the balance, and return an `invalid_params` error if it fails, consistently across all RPC/decoder paths that call `get_mint_owner_and_additional_data` or `get_parsed_token_account`/`get_parsed_token_accounts`.

### Proof of Concept
1. Create an `spl-token-2022` mint `M` with the `MintCloseAuthority` extension and decimals `D1`, using keypair `K`.
2. Create a real token account `A` for mint `M` and mint some tokens into it (owned by the real Token-2022 program), then reduce supply of `M` to zero.
3. Close mint `M` via the `MintCloseAuthority` (this frees the account at pubkey `K`).
4. Using keypair `K`, recreate an account at the same address but owned by a different (e.g. attacker-controlled) program, writing 82 bytes of data crafted to unpack as a valid `Mint` with a different `decimals` value `D2`.
5. Call `getTokenAccountBalance` for account `A` — the RPC will report the balance using `D2` decimals instead of the original `D1`, despite the fetched mint account no longer being owned by any SPL Token program, since the owner check is skipped at rpc/src/rpc.rs:2032.

### Citations

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

**File:** rpc/src/rpc.rs (L2081-2087)
```rust
        let bank = self.bank(commitment);
        let (mint_owner, data) = get_mint_owner_and_additional_data(&bank, &mint)?;
        if !is_known_spl_token_id(&mint_owner) {
            return Err(Error::invalid_params(
                "Invalid param: not a Token mint".to_string(),
            ));
        }
```

**File:** rpc/src/parsed_token_accounts.rs (L92-107)
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
```
