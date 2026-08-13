The mock program itself has no in-program staleness gate (`solend_mocks` is a stub module with no instruction handlers, so all deposit logic executes via the CPI helper macros generated in `cpi.rs`, not an on-chain check). The staleness enforcement in this codebase lives entirely in the marginfi-side Anchor account constraints, and `SolendInitObligation` (`programs/marginfi/src/instructions/solend/init_obligation.rs`) is missing that constraint while `SolendDeposit` and `SolendWithdraw` both have it.

### Title
Missing staleness check on `integration_acc_1` allows Solend obligation bootstrap with a stale reserve exchange rate - (File: programs/marginfi/src/instructions/solend/init_obligation.rs)

### Summary
`solend_init_obligation` performs the very first Solend deposit for a bank (via `cpi_solend_deposit`, which computes `liquidity_to_collateral`/uses the reserve's exchange rate) but never checks `integration_acc_1.load()?.is_stale()?`, unlike `SolendDeposit` and `SolendWithdraw` which both enforce `constraint = !integration_acc_1.load()?.is_stale()? @ MarginfiError::SolendReserveStale`. This lets any caller supply a stale `SolendMinimalReserve` account when bootstrapping the obligation.

### Finding Description
In `programs/marginfi/src/instructions/solend/init_obligation.rs`, the `SolendInitObligation` account struct declares `integration_acc_1` with only `has_one` and ownership implicit checks: [1](#0-0) 
No staleness constraint is present, in contrast to `SolendDeposit`: [2](#0-1) 
and `SolendWithdraw`: [3](#0-2) 
`solend_init_obligation` still uses the reserve's exchange rate implicitly through `cpi_solend_deposit`, which passes `integration_acc_1` as the `reserve_info` into the CPI call that performs `deposit_reserve_liquidity_and_obligation_collateral`: [4](#0-3) 
`SolendMinimalReserve::is_stale()` checks `last_update_slot < clock.slot`: [5](#0-4) 
Because the mock/CPI layer (`solend_mocks` program module) has no instruction handlers of its own enforcing staleness on-chain — it is an empty `#[program] pub mod solend_mocks {}` — the only staleness gate in this codebase is the Anchor account constraint on the marginfi side, and that gate is absent from `SolendInitObligation`. [6](#0-5) 

### Impact Explanation
An unprivileged caller can invoke `solend_init_obligation` with `amount = 10` while `integration_acc_1` is stale, locking in the bank's very first collateral/liquidity accounting basis using a distorted exchange rate. This is exactly the invariant enforced everywhere else (deposit/withdraw) — "oracle/price and reserve-state accounting must remain conservative at every entry point" — and its absence here creates a protocol inconsistency between the bank's bootstrap collateral basis and the reserve's true value, undermining downstream accounting for that bank's Solend integration.

### Likelihood Explanation
`solend_init_obligation` is a permissionless, reachable instruction (only requires a valid bank, mint, and a `fee_payer` signer with the required token balance) that any user can call once per bank to bootstrap its obligation. The precondition — the reserve has not been refreshed and its `last_update_slot < clock.slot` — is trivially satisfiable since refreshing is not enforced anywhere in this call path, making the bug reliably reproducible.

### Recommendation
Add the same constraint used in `SolendDeposit`/`SolendWithdraw` to the `integration_acc_1` field in `SolendInitObligation`:
```rust
#[account(
    mut,
    constraint = !integration_acc_1.load()?.is_stale()? @ MarginfiError::SolendReserveStale
)]
pub integration_acc_1: AccountLoader<'info, SolendMinimalReserve>,
```

### Proof of Concept
1. Unit/integration test setup: create a `SolendMinimalReserve` mock account with `last_update_slot` set below the current `Clock::get()?.slot` (i.e., `is_stale() == true`).
2. Call `solend_init_obligation(ctx, 10)` with this stale reserve as `integration_acc_1`.
3. Assert (current behavior, demonstrating the bug): the call succeeds without error, despite the reserve being stale.
4. Add the recommended `is_stale` constraint, re-run the same test, and assert the call now fails with `MarginfiError::SolendReserveStale`, matching the behavior already verified for `solend_deposit`/`solend_withdraw` staleness rejection tests.

### Citations

**File:** programs/marginfi/src/instructions/solend/init_obligation.rs (L98-100)
```rust
    /// CHECK: validated by the Solend program
    #[account(mut)]
    pub integration_acc_1: AccountLoader<'info, SolendMinimalReserve>,
```

**File:** programs/marginfi/src/instructions/solend/init_obligation.rs (L174-199)
```rust
    pub fn cpi_solend_deposit(&self, amount: u64, authority_bump: u8) -> MarginfiResult {
        let accounts = DepositReserveLiquidityAndObligationCollateral {
            source_liquidity_info: self.liquidity_vault.to_account_info(),
            user_collateral_info: self.user_collateral.to_account_info(),
            reserve_info: self.integration_acc_1.to_account_info(),
            reserve_liquidity_supply_info: self.reserve_liquidity_supply.to_account_info(),
            reserve_collateral_mint_info: self.reserve_collateral_mint.to_account_info(),
            lending_market_info: self.lending_market.to_account_info(),
            lending_market_authority_info: self.lending_market_authority.to_account_info(),
            destination_deposit_collateral_info: self.reserve_collateral_supply.to_account_info(),
            obligation_info: self.integration_acc_2.to_account_info(),
            obligation_owner_info: self.liquidity_vault_authority.to_account_info(),
            pyth_price_info: self.pyth_price.to_account_info(),
            switchboard_feed_info: self.switchboard_feed.to_account_info(),
            user_transfer_authority_info: self.liquidity_vault_authority.to_account_info(),
            token_program_info: self.token_program.to_account_info(),
        };
        let signer_seeds: &[&[&[u8]]] =
            bank_signer!(BankVaultType::Liquidity, self.bank.key(), authority_bump);

        // Create CPI context with signer
        let cpi_ctx =
            CpiContext::new_with_signer(self.solend_program.key(), accounts, signer_seeds);
        deposit_reserve_liquidity_and_obligation_collateral(cpi_ctx, amount)?;
        Ok(())
    }
```

**File:** programs/marginfi/src/instructions/solend/deposit.rs (L216-221)
```rust
    /// The Solend reserve that holds liquidity
    #[account(
        mut,
        constraint = !integration_acc_1.load()?.is_stale()? @ MarginfiError::SolendReserveStale
    )]
    pub integration_acc_1: AccountLoader<'info, SolendMinimalReserve>,
```

**File:** programs/marginfi/src/instructions/solend/withdraw.rs (L374-379)
```rust
    /// The Solend reserve that holds liquidity
    #[account(
        mut,
        constraint = !integration_acc_1.load()?.is_stale()? @ MarginfiError::SolendReserveStale
    )]
    pub integration_acc_1: AccountLoader<'info, SolendMinimalReserve>,
```

**File:** programs/solend-mocks/src/state.rs (L127-133)
```rust
    /// Check if reserve is stale
    pub fn is_stale(&self) -> Result<bool> {
        let clock = Clock::get()?;
        // let stale = self.last_update_stale != 0;
        let slot_expired = self.last_update_slot < clock.slot;
        Ok(slot_expired)
    }
```

**File:** programs/solend-mocks/src/lib.rs (L11-12)
```rust
#[program]
pub mod solend_mocks {}
```
