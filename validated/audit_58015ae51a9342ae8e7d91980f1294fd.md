### Title
Permanent freeze of Token-2022 bank liquidity via revocable `TransferHook` extension - (File: `programs/marginfi/src/state/bank.rs`, `programs/marginfi/src/utils/general.rs`)

### Summary
Marginfi supports Token-2022 mints as bank collateral, including mints with the `TransferHook` extension, by routing deposits/withdrawals/borrows/repays through `spl_token_2022::onchain::invoke_transfer_checked`, which automatically CPIs into the mint's configured transfer-hook program using the `remaining_accounts`. Unlike the tokenfactory `BeforeSendHook`, this is not restricted to a whitelist: any Token-2022 mint with an active transfer hook can be used to back a bank, and the hook program/authority is entirely controlled by the mint's `TransferHook` extension update authority, external to marginfi's control. If that authority repoints the hook to an invalid or intentionally-reverting program after users have already deposited, every subsequent `deposit_spl_transfer`/`withdraw_spl_transfer` call for that bank's liquidity vault CPI will fail, permanently freezing the funds already held in the vault. This mirrors the report's root cause: a transfer-gating hook, controllable by an untrusted party, that governs mandatory internal accounting transfers with no safety valve.

### Finding Description
`deposit_spl_transfer` and `withdraw_spl_transfer` in `programs/marginfi/src/state/bank.rs` (around lines 688-851) invoke `spl_token_2022::onchain::invoke_transfer_checked` whenever the bank's mint is a Token-2022 mint (`maybe_mint.is_some()`), which natively performs the `TransferHook` interface CPI baked into that SPL primitive. Marginfi explicitly builds test infrastructure (`spl_transfer_hook_interface::offchain::add_extra_account_metas_for_execute` in `test-utils/src/marginfi_account.rs`) to support deposit/borrow/liquidation flows for mints carrying an active transfer hook, confirming this path is a first-class supported feature, not merely tolerated. [1](#0-0) [2](#0-1) 

`has_transfer_hook` in `programs/marginfi/src/utils/general.rs` (lines 133-149) is only invoked from `lending_pool_emissions_deposit` in `configure_bank.rs`, where an active hook causes an outright rejection (`MarginfiError::InvalidTransfer`). There is no equivalent check gating standard deposit/withdraw/borrow/repay flows, nor any check at bank-add time rejecting mints that carry (or could later carry) an active `TransferHook` program. [3](#0-2) [4](#0-3) 

The Token-2022 `TransferHook` extension's program pointer is mutable by its designated `authority` (a standard SPL feature, external to marginfi, akin to the tokenfactory `BeforeSendHook` being settable by the token creator at will). Because marginfi's transfer helper unconditionally performs the hook CPI for any Token-2022 mint with a hook set, an authority who initially sets a legitimate/no-op hook (to pass onboarding/testing) can later update it to an invalid address or an always-reverting program. From that point on, every `invoke_transfer_checked` call touching that bank's liquidity/insurance/fee vaults fails, exactly as in the tokenfactory report where `BeforeSendHook` was repointed to an EOA to force `no such contract` errors on every transfer. [5](#0-4) 

### Impact Explanation
Once the hook is weaponized, users who already deposited into that Token-2022-backed bank cannot withdraw, and borrowers cannot repay through the standard SPL-transfer codepath — the on-chain vault CPI will unconditionally fail because it must call through the hook program. This durably locks already-deposited principal, mirroring impact #1 in the reference report (stuck delegator funds), but here at the bank/vault level rather than validator-reward level. Because interest accrual and other bank-level state changes are not gated on this transfer succeeding elsewhere in the protocol (unlike the Cosmos SDK's `BeforeDelegationSharesModified` staking hook), this does not appear to cascade into a full-chain halt as in the reference report's impact #2; the blast radius is scoped to the specific Token-2022 bank and its depositors/borrowers, not the entire marginfi group or all banks.

### Likelihood Explanation
Likelihood depends on marginfi's governance actually permitting the addition of Token-2022 banks whose mint carries (or can later carry) an active `TransferHook` — bank-mint approval is admin-gated in `lending_pool_add_bank`, and marginfi could refuse to onboard third-party/attacker-controlled T22 mints. This somewhat limits the scenario to sanctioned integrations where the mint's transfer-hook update authority is not fully trusted (e.g., a partner token later becomes malicious or is compromised), or cases where governance approves a mint without realizing the hook authority is mutable and independent of marginfi. Given `has_transfer_hook` is already used defensively for emissions deposits but not for the core deposit/withdraw/borrow/repay path, this looks like an inconsistently-applied mitigation rather than a deliberate acceptance of risk for those flows — raising the likelihood that this gap was unintentional. I could not verify from the available files whether `lending_pool_add_bank` performs any transfer-hook check at bank-creation time; this remains uncertain and would need to be checked directly in the repository.

### Recommendation
Apply the same defensive check used in `lending_pool_emissions_deposit` (`utils::has_transfer_hook`) consistently at bank-admission time (`lending_pool_add_bank`/`configure_bank`) and/or before executing `deposit_spl_transfer`/`withdraw_spl_transfer` for Token-2022 mints, rejecting or flagging mints with an active/mutable transfer hook unless the hook program is verified immutable or explicitly whitelisted. Alternatively, require that any approved T22 mint's `TransferHook` authority be set to `None` (frozen) before bank approval, removing the ability for a third party to weaponize the hook after users have deposited.

### Proof of Concept
Conceptual PoC (mirrors the reference report's structure, adapted to marginfi/Solana):
1. Attacker creates a Token-2022 mint with the `TransferHook` extension pointing to a legitimate/no-op hook program, and gets it approved as a marginfi bank mint (or the mint is a pre-approved token whose hook authority is not marginfi-controlled).
2. Users deposit into the bank via `lending_account_deposit`, which succeeds because the hook currently passes.
3. The `TransferHook` extension's authority updates the hook's `program_id` to an invalid/non-existent program (or one that always returns an error), using the standard SPL Token-2022 `transfer_hook::instruction::update` instruction — a call entirely outside marginfi's program.
4. Subsequent calls to `lending_account_withdraw` / `lending_account_repay` for that bank fail inside `withdraw_spl_transfer`'s `spl_token_2022::onchain::invoke_transfer_checked` call because the mandatory hook CPI errors out, permanently freezing all liquidity already in the bank's vaults for that mint. [6](#0-5)

### Citations

**File:** programs/marginfi/src/state/bank.rs (L736-748)
```rust
        #[cfg(not(feature = "client"))]
        if let Some(mint) = maybe_mint {
            spl_token_2022::onchain::invoke_transfer_checked(
                program.key,
                from,
                mint.to_account_info(),
                to,
                authority,
                remaining_accounts,
                amount,
                mint.decimals,
                &[],
            )?;
```

**File:** programs/marginfi/src/state/bank.rs (L816-828)
```rust
        #[cfg(not(feature = "client"))]
        if let Some(mint) = maybe_mint {
            spl_token_2022::onchain::invoke_transfer_checked(
                program.key,
                from,
                mint.to_account_info(),
                to,
                authority,
                remaining_accounts,
                amount,
                mint.decimals,
                signer_seeds,
            )?;
```

**File:** test-utils/src/marginfi_account.rs (L227-258)
```rust
        #[cfg(feature = "transfer-hook")]
        {
            // If t22 with transfer hook, add remaining accounts
            let banks_client = self.ctx.borrow().banks_client.clone();
            let fetch_account_data_fn = move |key| {
                let mut banks_client = banks_client.clone();
                async move {
                    banks_client
                        .get_account(key)
                        .await
                        .map(|acc| acc.map(|a| a.data))
                }
            };
            let payer = self.ctx.borrow().payer.pubkey();
            if bank.mint.token_program == anchor_spl::token_2022::ID {
                // TODO: do that only if hook exists
                println!(
                    "[TODO] Adding extra account metas for execute for mint {:?}",
                    bank.mint.key
                );
                let _ = spl_transfer_hook_interface::offchain::add_extra_account_metas_for_execute(
                    &mut ix,
                    &TEST_HOOK_ID,
                    &funding_account,
                    &bank.mint.key,
                    &bank.get_vault(BankVaultType::Liquidity).0,
                    &payer,
                    ui_to_native!(ui_amount.into(), bank.mint.mint.decimals),
                    fetch_account_data_fn,
                )
                .await;
            }
```

**File:** programs/marginfi/src/utils/general.rs (L133-149)
```rust
/// Returns `true` if the given mint has an active transfer hook program.
/// If the hook is present but no program is active it would return false.
pub fn has_transfer_hook(mint_ai: AccountInfo) -> MarginfiResult<bool> {
    if mint_ai.owner.eq(&Token::id()) {
        return Ok(false);
    }

    let mint_data = mint_ai.try_borrow_data()?;
    let mint = StateWithExtensions::<spl_token_2022::state::Mint>::unpack(&mint_data)?;

    if let Ok(hook) = mint.get_extension::<TransferHook>() {
        let program_id: Option<Pubkey> = Option::from(hook.program_id);
        return Ok(program_id.is_some());
    }

    Ok(false)
}
```

**File:** programs/marginfi/src/instructions/marginfi_group/configure_bank.rs (L100-109)
```rust
    // Reject mints with non-zero transfer fees or active transfer hooks.
    let mint_ai = ctx.accounts.mint.to_account_info();
    check!(
        !utils::nonzero_fee(mint_ai.clone(), clock.epoch)?,
        MarginfiError::InvalidTransfer
    );
    check!(
        !utils::has_transfer_hook(mint_ai)?,
        MarginfiError::InvalidTransfer
    );
```
