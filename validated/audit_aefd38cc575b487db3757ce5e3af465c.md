### Title
Front-runnable, unauthenticated `init_global_fee_state` allows an attacker to seize the protocol's `global_fee_admin` role - ([File: programs/marginfi/src/instructions/marginfi_group/init_global_fee_state.rs])

### Summary
The `Cover.deploy` report flags a permissionless setup/creation function that lacks an `onlyOwner` modifier, letting an unprivileged caller invoke privileged initialization logic. The marginfi-v2 program has a direct analog: `init_global_fee_state` (and its v2 counterpart `init_global_fee_state_v2`) initializes the single, program-wide `FeeState` PDA and sets an arbitrary, caller-supplied `admin_key` as `global_fee_admin`, with no check that the caller is the legitimate deployer/multisig.

### Finding Description
`init_global_fee_state` is exposed in `programs/marginfi/src/lib.rs` with the comment "(Runs once per program)" [1](#0-0) , and its handler unconditionally writes the caller-supplied `admin_key` into `fee_state.global_fee_admin` and `fee_wallet` into `fee_state.global_fee_wallet`: [2](#0-1) 

The associated `Accounts` struct only requires `payer: Signer<'info>` — any signer, not a specific deployer/multisig key — and relies solely on the PDA `init` constraint (`seeds = [FEE_STATE_SEED]`) to make the call "run once": [3](#0-2) 

Because the `FeeState` account address is a deterministic PDA (`FEE_STATE_SEED`, no signer/keypair needed) and the instruction has no owner/authority gate, whoever's transaction lands first — not necessarily the deploying team — becomes `global_fee_admin`. This is analogous to `Cover.deploy` being callable by anyone: a state-establishing/administrative entry point that should be restricted to the deployer/owner is left fully permissionless.

Downstream, `global_fee_admin` is a durable, high-privilege role: `edit_global_fee_state` is gated by `has_one = global_fee_admin` in `programs/marginfi/src/instructions/marginfi_group/edit_global_fee.rs`, and it can change `global_fee_wallet`, `program_fee_fixed`, `program_fee_rate`, `bank_init_flat_sol_fee`, `liquidation_flat_sol_fee`, `order_init_flat_sol_fee`, `liquidation_max_fee`, `order_execution_max_fee`, and `pause_delegate_admin` [4](#0-3) . These values are propagated into every `MarginfiGroup` via the permissionless `propagate_fee` instruction, which copies `fee_state.global_fee_wallet`, `program_fee_fixed`, and `program_fee_rate` into each group's `fee_state_cache` with no admin check on the caller of `propagate_fee` itself (it's documented as usable on "any group") [5](#0-4) .

### Impact Explanation
If an attacker races the legitimate deployment sequence and successfully calls `init_global_fee_state` first, they permanently seize the `global_fee_admin` role for the entire protocol (the PDA can only be initialized once, so this cannot be "fixed" by simply re-running the setup). With that role they can subsequently:
- Redirect all protocol/program fee collection to an attacker-controlled `global_fee_wallet`.
- Set `program_fee_fixed`/`program_fee_rate` and flat SOL fees to attacker-favorable (or protocol-destructive) values across every group via `propagate_fee`.
- Set `pause_delegate_admin` to an attacker key, gaining protocol-pause capability.

This is a durable authorization bypass with direct, protocol-wide financial impact (misdirected fee revenue and control over global fee/pause parameters), not merely a cosmetic permission gap.

### Likelihood Explanation
Exploitation requires the attacker to observe the newly-deployed (but not-yet-initialized) marginfi program on-chain and submit a `init_global_fee_state` transaction before the legitimate operator's initialization transaction lands — a classic "front-run program initialization" race that is well understood and has been exploited against other Solana programs. Because the `FeeState` PDA address is fully deterministic from the immutable seed `FEE_STATE_SEED` and the program ID (both public as soon as the program binary is deployed), an attacker can pre-compute the target PDA and submit their transaction proactively, requiring no privileged information — only priority/latency in landing the transaction after deployment and before the deploy team's own init call.

### Recommendation
Restrict `init_global_fee_state` (and `init_global_fee_state_v2`) to a hardcoded/known deployer key (e.g., a compile-time constant multisig/program-upgrade-authority pubkey), or require that `payer`/the account setting `admin_key` match the program's upgrade authority (verified via the `ProgramData` account), analogous to adding an `onlyOwner` modifier to `Cover.deploy`. Alternatively, bundle the fee-state initialization atomically with program deployment (e.g., invoked only from within the same multisig transaction that performs the deploy) so there is no window in which an unauthenticated actor can win the race.

### Proof of Concept
1. Attacker monitors the chain for the marginfi program's deployment (program ID is public once the buffer is set live).
2. Attacker computes the deterministic `FeeState` PDA: `Pubkey::find_program_address(&[FEE_STATE_SEED.as_bytes()], &marginfi::ID)`.
3. Before the legitimate operator's `init_global_fee_state` transaction lands, the attacker submits their own transaction calling `init_global_fee_state(ctx, attacker_admin_key, attacker_fee_wallet, ...)`, signed only by an arbitrary `payer` (no privileged signer required) — see the accounts constraints, which only require `payer: Signer<'info>`: [3](#0-2) 
4. Because the PDA does not yet exist, Anchor's `init` succeeds and `fee_state.global_fee_admin = attacker_admin_key` is committed permanently. The legitimate operator's later attempt to call the same instruction fails since the PDA already exists.
5. The attacker now calls `edit_global_fee_state` (gated only by `has_one = global_fee_admin`) to set `fee_wallet` to their own wallet and adjust fee rates, which are then propagated to every group via the permissionless `propagate_fee` instruction.

### Citations

**File:** programs/marginfi/src/lib.rs (L565-591)
```rust
    /// (Runs once per program) Configures the fee state account, where the global admin sets fees
    /// that are assessed to the protocol
    pub fn init_global_fee_state(
        ctx: Context<InitFeeState>,
        admin: Pubkey,
        fee_wallet: Pubkey,
        bank_init_flat_sol_fee: u32,
        liquidation_flat_sol_fee: u32,
        order_init_flat_sol_fee: u32,
        program_fee_fixed: WrappedI80F48,
        program_fee_rate: WrappedI80F48,
        liquidation_max_fee: WrappedI80F48,
        order_execution_max_fee: WrappedI80F48,
    ) -> MarginfiResult {
        marginfi_group::initialize_fee_state(
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
        )
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

**File:** programs/marginfi/src/instructions/marginfi_group/propagate_fee_state.rs (L1-43)
```rust
use anchor_lang::prelude::*;
use marginfi_type_crate::{
    constants::FEE_STATE_SEED,
    types::{FeeState, MarginfiGroup},
};

#[derive(Accounts)]
pub struct PropagateFee<'info> {
    // Note: there is just one FeeState per program, so no further check is required.
    #[account(
        seeds = [FEE_STATE_SEED.as_bytes()],
        bump,
    )]
    pub fee_state: AccountLoader<'info, FeeState>,

    /// Any group, this ix is permisionless and can propagate the fee to any group
    #[account(mut)]
    pub marginfi_group: AccountLoader<'info, MarginfiGroup>,
}

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
