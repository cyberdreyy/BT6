### Title
Unprotected `FeeState` initializer allows front-running to seize global fee admin - ([File: programs/marginfi/src/instructions/marginfi_group/init_global_fee_state.rs])

### Summary
`initialize_fee_state` (instruction `InitFeeState`), which creates the single global `FeeState` PDA for the entire marginfi program, has no authorization check on the caller. Any signer can invoke it and pass arbitrary `admin_key` / `fee_wallet` values, since the accounts struct only requires a `payer: Signer` and does not check against any hardcoded deployer/upgrade authority.

### Finding Description
`InitFeeState` derives a single global PDA from `FEE_STATE_SEED` alone (no per-group/per-user component), so exactly one `FeeState` account can ever exist for the whole program. [1](#0-0) 

The handler sets `fee_state.global_fee_admin = admin_key` and `fee_state.global_fee_wallet = fee_wallet` directly from instruction arguments with no `has_one`, no comparison to a known governance key, and no check that `payer` is any particular authority: [2](#0-1) 

This is the exact bug class described in the report: a second/standalone initializer that is not executed atomically with deployment and lacks protection against unauthorized callers. Because the PDA address is fully deterministic from the program ID (`[FEE_STATE_SEED]`), any party monitoring the mempool/validator for the program's deployment (or simply racing the legitimate initialization transaction before it lands) can submit their own `InitFeeState` call first and permanently become `global_fee_admin`, redirecting `global_fee_wallet` to an address they control.

Once `global_fee_admin` is attacker-controlled, `EditFeeState` (`edit_fee_state`) is gated only by `has_one = global_fee_admin`, so the attacker can subsequently rewrite `global_fee_wallet`, `program_fee_fixed`, `program_fee_rate`, `bank_init_flat_sol_fee`, `liquidation_flat_sol_fee`, `pause_delegate_admin`, etc., at will: [3](#0-2) 

The companion `FeeStateV2` initializer has the identical defect — no admin/owner check, single global PDA, `payer` only: [4](#0-3) 

### Impact Explanation
`global_fee_admin`/`global_fee_wallet` are protocol-wide, financially meaningful values: `program_fee_fixed`/`program_fee_rate` are the program's take from every group's fee cache and `global_fee_wallet` is the destination of those collected protocol fees. If the initializer is front-run, an attacker permanently controls the protocol fee configuration and fee destination wallet for the entire deployed program (all groups pull the fee-state cache from this account via `propagate_fee`), enabling redirection of protocol fee revenue and manipulation of protocol fee rates — durable financial impact requiring a costly program redeployment/migration to fix, since the PDA cannot be re-initialized once created.

### Likelihood Explanation
Exploitation requires only observing/racing a single deterministic-address transaction before the legitimate deployer executes it (classic init-frontrunning on Solana), and can be attempted by anyone with a wallet capable of paying the small SPL rent/txn fee — no privileged role, staked collateral, or special integration access is needed. Likelihood is somewhat mitigated by deployers typically pairing program deploy and initialization in the same script/transaction bundle, but the report's own recommendation ("provide a deployment/upgrade script with transparent call") underscores that this is exactly the scenario the report flags as at-risk absent an on-chain protection.

### Recommendation
Add an on-chain authorization check to `InitFeeState` (and `InitFeeStateV2`), e.g. require `payer` to match a hardcoded/immutable authority (such as the program's upgrade authority via `#[account(constraint = ...)]`, or a value baked into `declare_id!`/a constants module), or perform the fee-state initialization atomically within program deployment tooling and add a guard rejecting any subsequent re-initialization attempt from an unexpected signer. At minimum, ensure deployment scripts submit the `InitFeeState` transaction in the same atomic bundle as program deployment/upgrade so no window exists for front-running.

### Proof of Concept
1. Observe the marginfi program deployment (program ID is public immediately upon deploy).
2. Before the legitimate team submits their `InitFeeState`/`InitFeeStateV2` transaction, submit a transaction calling `initialize_fee_state` (or `initialize_fee_state_v2`) with `admin_key = attacker_pubkey`, `fee_wallet = attacker_wallet`, and any values for the fee fields, funded by any wallet as `payer`.
3. Because `fee_state`/`fee_state_v2` are `init`-constrained PDAs at deterministic addresses (`[FEE_STATE_SEED]` / `[FEE_STATE_V2_SEED]`), the attacker's transaction succeeds, permanently setting `global_fee_admin` to the attacker.
4. The legitimate team's later `InitFeeState` call fails (`already in use`), and the attacker now calls `edit_fee_state` freely (passing `has_one = global_fee_admin`) to redirect `global_fee_wallet` and manipulate `program_fee_fixed`/`program_fee_rate` for the whole protocol.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/init_global_fee_state.rs (L9-35)
```rust
pub fn initialize_fee_state(
    ctx: Context<InitFeeState>,
    admin_key: Pubkey,
    fee_wallet: Pubkey,
    bank_init_flat_sol_fee: u32,
    liquidation_flat_sol_fee: u32,
    order_init_flat_sol_fee: u32,
    program_fee_fixed: WrappedI80F48,
    program_fee_rate: WrappedI80F48,
    liquidation_max_fee: WrappedI80F48,
    order_execution_max_fee: WrappedI80F48,
) -> Result<()> {
    let mut fee_state = ctx.accounts.fee_state.load_init()?;
    fee_state.global_fee_admin = admin_key;
    fee_state.global_fee_wallet = fee_wallet;
    fee_state.key = ctx.accounts.fee_state.key();
    fee_state.bank_init_flat_sol_fee = bank_init_flat_sol_fee;
    fee_state.bump_seed = ctx.bumps.fee_state;
    fee_state.program_fee_fixed = program_fee_fixed;
    fee_state.program_fee_rate = program_fee_rate;
    fee_state.liquidation_max_fee = liquidation_max_fee;
    fee_state.liquidation_flat_sol_fee = liquidation_flat_sol_fee;
    fee_state.order_execution_max_fee = order_execution_max_fee;
    fee_state.order_init_flat_sol_fee = order_init_flat_sol_fee;

    Ok(())
}
```

**File:** programs/marginfi/src/instructions/marginfi_group/init_global_fee_state.rs (L37-55)
```rust
#[derive(Accounts)]
pub struct InitFeeState<'info> {
    /// Pays the init fee
    #[account(mut)]
    pub payer: Signer<'info>,

    #[account(
        init,
        seeds = [
            FEE_STATE_SEED.as_bytes()
        ],
        bump,
        payer = payer,
        space = 8 + FeeState::LEN,
    )]
    pub fee_state: AccountLoader<'info, FeeState>,

    pub system_program: Program<'info, System>,
}
```

**File:** programs/marginfi/src/instructions/marginfi_group/edit_global_fee.rs (L108-121)
```rust
#[derive(Accounts)]
pub struct EditFeeState<'info> {
    /// Admin of the global FeeState
    pub global_fee_admin: Signer<'info>,

    // Note: there is just one FeeState per program, so no further check is required.
    #[account(
        mut,
        seeds = [FEE_STATE_SEED.as_bytes()],
        bump,
        has_one = global_fee_admin @ MarginfiError::Unauthorized
    )]
    pub fee_state: AccountLoader<'info, FeeState>,
}
```

**File:** programs/marginfi/src/instructions/marginfi_group/init_global_fee_state_v2.rs (L1-31)
```rust
use anchor_lang::prelude::*;
use marginfi_type_crate::{constants::FEE_STATE_V2_SEED, types::FeeStateV2};

/// Runs once per program to initialize the V2 fee state account.
pub fn initialize_fee_state_v2(ctx: Context<InitFeeStateV2>) -> Result<()> {
    let mut fee_state_v2 = ctx.accounts.fee_state_v2.load_init()?;
    fee_state_v2.key = ctx.accounts.fee_state_v2.key();
    fee_state_v2.bump_seed = ctx.bumps.fee_state_v2;

    Ok(())
}

#[derive(Accounts)]
pub struct InitFeeStateV2<'info> {
    /// Pays the init fee
    #[account(mut)]
    pub payer: Signer<'info>,

    #[account(
        init,
        seeds = [FEE_STATE_V2_SEED.as_bytes()],
        bump,
        payer = payer,
        space = 8 + FeeStateV2::LEN,
    )]
    pub fee_state_v2: AccountLoader<'info, FeeStateV2>,

    pub system_program: Program<'info, System>,
}


```
