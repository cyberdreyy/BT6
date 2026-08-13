### Title
Missing `is_protocol_paused()` check in `SolendInitObligation` allows obligation initialization during a protocol pause - ([File: programs/marginfi/src/instructions/solend/init_obligation.rs])

### Finding Description
`solend_init_obligation` (programs/marginfi/src/instructions/solend/init_obligation.rs:16-35) initializes a Solend obligation PDA for a bank and performs a nominal deposit into it via CPI. Its account struct `SolendInitObligation` (lines 37-139) has no `group: AccountLoader<'info, MarginfiGroup>` field at all, and consequently no `constraint = !group.load()?.is_protocol_paused() @ MarginfiError::ProtocolPaused` guard. [1](#0-0) 

This is inconsistent with every other Solend/Kamino/Juplend integration entrypoint that touches funds or state, all of which explicitly bind a `group` account with the pause constraint, e.g. `SolendDeposit`: [2](#0-1) 

and `SolendWithdraw`: [3](#0-2) 

The `bank` account in `SolendInitObligation` also lacks a `has_one = group` constraint (unlike `SolendDeposit`'s bank), so there is no indirect way to bind or check the group's paused state at all. The `fee_payer` field is only a `Signer<'info>` with no admin/authority constraint, meaning any unprivileged wallet can supply an arbitrary `bank` (any account satisfying the `is_solend_asset_tag` and `has_one` constraints) and directly call the instruction. Since no pause check exists anywhere in the call path, `Bank::load()` succeeds, the min-amount check (`amount >= 10`) passes trivially, and the CPI sequence (`cpi_init_obligation`, `cpi_transfer_user_to_liquidity_vault`, `cpi_solend_deposit`) executes and durably creates and activates a Solend obligation for that bank while the group is paused.

### Impact Explanation
An admin-declared pause is intended to freeze all new integration activity for a group. This instruction allows an unprivileged caller to bootstrap (permanently initialize) a Solend obligation for a not-yet-bootstrapped bank during a pause window, which is a durable, irreversible state change (the obligation PDA is created with `init`, so it cannot be re-initialized, and the nominal deposit is explicitly documented as irrecoverable: "This amount is irrecoverable and will prevent the obligation from ever being closed."). This directly violates the invariant that pause must halt all new integration activity, and produces a durable protocol-state divergence from the admin's declared intent, matching the "scoped impact" described in the question.

### Likelihood Explanation
- Preconditions: `group.is_protocol_paused() == true`, target bank exists with Solend asset tag but obligation (`integration_acc_2` PDA) not yet initialized.
- Feasibility: any signer can act as `fee_payer` with no privilege requirement; all other accounts are either PDAs derivable from the bank or Solend program-validated. No admin/governance/oracle-operator access is required.
- Repeatability: exploitable once per not-yet-bootstrapped bank (limited by the `init` constraint on `integration_acc_2`), but each new Solend-tagged bank added while the group happens to be paused is independently exploitable.

### Recommendation
Add a `group: AccountLoader<'info, MarginfiGroup>` field to `SolendInitObligation` bound via `has_one = group` on `bank`, with the same `constraint = !group.load()?.is_protocol_paused() @ MarginfiError::ProtocolPaused` used in `SolendDeposit`/`SolendWithdraw`. Apply the same fix to any sibling `*_init_obligation`/init-lending instructions across integrations (e.g. Kamino's `init_obligation.rs`) if they share this omission.

### Proof of Concept
Rust integration test plan (using existing Solend test harness, e.g. `tests/specs/solend/sl05_solendMrgnBank.spec.ts` / `test-utils/src/test.rs` patterns):
1. Set up a marginfi group and a Solend-tagged bank that has not yet had `solend_init_obligation` called.
2. As group admin, call the pause instruction to set `group.is_protocol_paused() == true` (verify via group state).
3. As an unprivileged test wallet (not admin), construct and submit `solend_init_obligation` with a valid `fee_payer`, `bank`, and correctly derived PDAs (`integration_acc_2`, `liquidity_vault_authority`), amount = 10.
4. Assert the transaction succeeds and the Solend obligation account is created and owned by `SOLEND_PROGRAM_ID`, with a non-zero deposited balance — demonstrating the pause was bypassed.
5. Compare with an equivalent call to `solend_deposit` under the same paused group, which should fail with `MarginfiError::ProtocolPaused`, establishing the inconsistency. [4](#0-3)

### Citations

**File:** programs/marginfi/src/instructions/solend/init_obligation.rs (L16-35)
```rust
pub fn solend_init_obligation(ctx: Context<SolendInitObligation>, amount: u64) -> MarginfiResult {
    // Arbitrarily setting minimum deposit to 10 absolute units to always keep the obligation alive.
    // Obligations auto close when empty, but we want to keep it open for future deposits.
    require_gte!(amount, 10, MarginfiError::ObligationInitDepositInsufficient);

    let authority_bump = ctx.accounts.bank.load()?.liquidity_vault_authority_bump;

    // Initialize the obligation
    ctx.accounts.cpi_init_obligation(authority_bump)?;

    // Transfer tokens from user (signer_token_account) -> liquidity vault
    ctx.accounts.cpi_transfer_user_to_liquidity_vault(amount)?;

    // Deposit into Solend (liquidity vault) -> (reserve_liquidity_supply)
    ctx.accounts.cpi_solend_deposit(amount, authority_bump)?;

    msg!("Solend obligation initialized with amount: {}", amount);

    Ok(())
}
```

**File:** programs/marginfi/src/instructions/solend/init_obligation.rs (L37-52)
```rust
#[derive(Accounts)]
pub struct SolendInitObligation<'info> {
    /// Pays to init the obligation and pays a nominal amount to ensure the obligation has a
    /// non-zero balance.
    #[account(mut)]
    pub fee_payer: Signer<'info>,

    #[account(
        has_one = liquidity_vault @ MarginfiError::InvalidLiquidityVault,
        has_one = integration_acc_1 @ MarginfiError::InvalidSolendReserve,
        has_one = integration_acc_2 @ MarginfiError::InvalidSolendObligation,
        has_one = mint @ MarginfiError::InvalidMint,
        constraint = is_solend_asset_tag(bank.load()?.config.asset_tag)
            @ MarginfiError::WrongBankAssetTagForSolendOperation
    )]
    pub bank: AccountLoader<'info, Bank>,
```

**File:** programs/marginfi/src/instructions/solend/deposit.rs (L145-152)
```rust
#[derive(Accounts)]
pub struct SolendDeposit<'info> {
    #[account(
        constraint = (
            !group.load()?.is_protocol_paused()
        ) @ MarginfiError::ProtocolPaused
    )]
    pub group: AccountLoader<'info, MarginfiGroup>,
```

**File:** programs/marginfi/src/instructions/solend/withdraw.rs (L290-298)
```rust
#[derive(Accounts)]
pub struct SolendWithdraw<'info> {
    #[account(
        constraint = (
            !group.load()?.is_protocol_paused()
            || marginfi_account.load()?.get_flag(ACCOUNT_IN_DELEVERAGE)
        ) @ MarginfiError::ProtocolPaused
    )]
    pub group: AccountLoader<'info, MarginfiGroup>,
```
