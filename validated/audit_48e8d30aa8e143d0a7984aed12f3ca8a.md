### Title
Unprivileged front-running of `init_global_fee_state` allows attacker to seize the protocol's global fee admin role - ([File: programs/marginfi/src/instructions/marginfi_group/init_global_fee_state.rs])

### Summary
The Notional finding describes an uninitialized UUPS implementation contract where any unprivileged user can call `initialize()` first and seize the `onlyOwner` role. marginfi-v2 has a structurally analogous pattern in `init_global_fee_state`: the program-wide `FeeState` PDA is created via a plain `init` account constraint with **no signer/authority check** on who may call it, and the caller-supplied `admin_key` argument is written directly into the account as the permanent `global_fee_admin`.

### Finding Description
`InitFeeState`'s `initialize_fee_state` handler is documented as "Runs once per program to init the global fee state," implying it is meant to be called exactly once by the deployer/team. However, the accounts struct enforces no such restriction: [1](#0-0) 

`payer` is merely `Signer<'info>` — any wallet can sign — and the `fee_state` PDA is derived solely from the fixed seed `FEE_STATE_SEED`, with no comparison against a hardcoded admin, the program's upgrade authority, or any other privileged key: [2](#0-1) 

The handler blindly trusts the caller-supplied `admin_key` and `fee_wallet` parameters and writes them into the account: [3](#0-2) 

Because Anchor's `init` constraint fails if the PDA already exists, this is a strict "first writer wins" race — exactly the same root cause as the Notional bug (an unprotected, callable-once `initialize()`/constructor-equivalent that lets whoever calls it first become the privileged owner). The `global_fee_admin` set here subsequently gates privileged instructions such as `edit_global_fee_state` (which can change the admin, fee wallet, and `pause_delegate_admin`) and `config_group_fee` (which can toggle protocol fees for any group): [4](#0-3) [5](#0-4) 

Every `MarginfiGroup` also derives from this PDA and caches its `global_fee_wallet`/fee parameters at group-init and via the permissionless `propagate_fee_state` instruction, so hijacking `FeeState` propagates the attacker's fee-wallet address and fee rates protocol-wide: [6](#0-5) [7](#0-6) 

### Impact Explanation
An unprivileged attacker who observes the deploy transaction sequence (or monitors any redeployment / disaster-recovery redeploy that recreates this PDA) can front-run the legitimate `init_global_fee_state` call with their own transaction, setting themselves as `global_fee_admin` and pointing `global_fee_wallet` at an address they control. From there they can edit fee parameters to the maximum allowed, redirect protocol fee collection to themselves via `edit_global_fee_state`/`propagate_fee_state`, and control the `pause_delegate_admin`, all without ever holding the group admin or program upgrade key. This is a durable, financially-impactful authorization bypass at the top of marginfi's admin hierarchy, not merely a denial of service.

### Likelihood Explanation
Exploitation requires the attacker to win a one-shot creation race against the deployer for a single global PDA. On a network with public mempool visibility (or simply by attempting the call speculatively before the real `init_global_fee_state` transaction lands), this is realistically achievable for any redeployment scenario (new clusters, disaster recovery, forks, or future protocol instances that reuse this instruction), even though the current mainnet `FeeState` is already initialized and thus not presently exploitable. The vulnerability class is reachable by any unprivileged signer and requires no privileged access, so it is not excluded by SECURITY.md's "impacts requiring access to privileged addresses" carve-out.

### Recommendation
Restrict `init_global_fee_state` (and `init_global_fee_state_v2`) to a hardcoded, trusted key (e.g., a `#[account(constraint = payer.key() == HARDCODED_DEPLOYER)]` check, or gating via the program's upgrade authority read from `ProgramData`), or initialize the PDA atomically as part of the program's deployment transaction so no window for front-running exists.

### Proof of Concept
1. Attacker monitors the mempool/transaction history for the program's deployment.
2. Before the legitimate team's `init_global_fee_state` transaction confirms, the attacker submits their own `init_global_fee_state(admin_key = attacker, fee_wallet = attacker, ...)` transaction targeting the same `FEE_STATE_SEED` PDA.
3. Anchor's `init` constraint succeeds for whichever transaction lands first; if the attacker's lands first, the legitimate deployer's subsequent `init_global_fee_state` call fails (account already initialized).
4. The attacker now controls `global_fee_admin` on the `FeeState` PDA and can call `edit_global_fee_state` to set fees/wallet/pause-delegate to attacker-controlled values, then call the permissionless `propagate_fee_state` against every `MarginfiGroup` to force adoption of these attacker-controlled parameters.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/init_global_fee_state.rs (L1-35)
```rust
// Runs once per program to init the global fee state.
use anchor_lang::prelude::*;
use marginfi_type_crate::{
    constants::FEE_STATE_SEED,
    types::{FeeState, WrappedI80F48},
};

#[allow(unused_variables)]
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

**File:** programs/marginfi/src/lib.rs (L603-630)
```rust
    /// (global fee admin only) Adjust fees, admin, wallet, or pause delegate admin
    pub fn edit_global_fee_state(
        ctx: Context<EditFeeState>,
        admin: Option<Pubkey>,
        fee_wallet: Option<Pubkey>,
        bank_init_flat_sol_fee: Option<u32>,
        liquidation_flat_sol_fee: Option<u32>,
        order_init_flat_sol_fee: Option<u32>,
        program_fee_fixed: Option<WrappedI80F48>,
        program_fee_rate: Option<WrappedI80F48>,
        liquidation_max_fee: Option<WrappedI80F48>,
        order_execution_max_fee: Option<WrappedI80F48>,
        pause_delegate_admin: Option<Pubkey>,
    ) -> MarginfiResult {
        marginfi_group::edit_fee_state(
            ctx,
            admin,
            fee_wallet,
            bank_init_flat_sol_fee,
            liquidation_flat_sol_fee,
            order_init_flat_sol_fee,
            program_fee_fixed,
            program_fee_rate,
            liquidation_max_fee,
            order_execution_max_fee,
            pause_delegate_admin,
        )
    }
```

**File:** programs/marginfi/src/lib.rs (L637-645)
```rust
    /// (global fee admin only) Enable or disable program fees for any group. Does not require the
    /// group admin to sign: the global fee state admin can turn program fees on or off for any
    /// group
    pub fn config_group_fee(
        ctx: Context<ConfigGroupFee>,
        enable_program_fee: bool,
    ) -> MarginfiResult {
        marginfi_group::config_group_fee(ctx, enable_program_fee)
    }
```

**File:** programs/marginfi/src/instructions/marginfi_group/initialize.rs (L21-41)
```rust
    let fee_state = ctx.accounts.fee_state.load()?;

    // The fuzzer should ignore this because the "Clock" mock sysvar doesn't load until after the
    // group is init. Eventually we might fix the fuzzer to load the clock first...
    #[cfg(not(feature = "client"))]
    {
        let clock = Clock::get()?;
        marginfi_group.fee_state_cache.last_update = clock.unix_timestamp;
    }
    marginfi_group.fee_state_cache.global_fee_wallet = fee_state.global_fee_wallet;
    marginfi_group.fee_state_cache.program_fee_fixed = fee_state.program_fee_fixed;
    marginfi_group.fee_state_cache.program_fee_rate = fee_state.program_fee_rate;
    marginfi_group.banks = 0;

    let cache = marginfi_group.fee_state_cache;
    msg!(
        "global fee wallet: {:?}, fixed fee: {:?}, program free {:?}",
        cache.global_fee_wallet,
        cache.program_fee_fixed,
        cache.program_fee_rate
    );
```

**File:** programs/marginfi/src/instructions/marginfi_group/propagate_fee_state.rs (L21-43)
```rust
pub fn propagate_fee(ctx: Context<PropagateFee>) -> Result<()> {
    let mut group = ctx.accounts.marginfi_group.load_mut()?;
    let fee_state = ctx.accounts.fee_state.load()?;

    group.fee_state_cache.global_fee_wallet = fee_state.global_fee_wallet;
    group.fee_state_cache.program_fee_fixed = fee_state.program_fee_fixed;
    group.fee_state_cache.program_fee_rate = fee_state.program_fee_rate;

    let clock = Clock::get()?;
    group.fee_state_cache.last_update = clock.unix_timestamp;

    group
        .panic_state_cache
        .update_from_panic_state(&fee_state.panic_state, clock.unix_timestamp);

    msg!(
        "Propagated fee and panic state to group. Panic state: paused={}",
        group.panic_state_cache.is_paused_flag()
            && !group.panic_state_cache.is_expired(clock.unix_timestamp)
    );

    Ok(())
}
```
