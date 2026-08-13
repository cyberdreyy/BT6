### Title
Permissionless front-runnable initialization of the singleton `FeeState` PDA allows an attacker to seize the global fee admin role - (File: `programs/marginfi/src/instructions/marginfi_group/init_global_fee_state.rs`)

### Summary
The Fractional finding describes an uninitialized `Vault` implementation whose `init()` carries no authorization check, letting any caller front-run legitimate initialization and become the privileged owner, which is then leveraged to permanently destroy the shared implementation. The strongest reachable analog in marginfi-v2 is the `init_global_fee_state` instruction, which creates the program-wide singleton `FeeState` PDA and sets an attacker-controlled `admin_key` parameter as `global_fee_admin`, with no constraint tying the caller or the resulting admin to any pre-approved authority.

### Finding Description
`init_global_fee_state` (`programs/marginfi/src/lib.rs:567-591`) is a completely permissionless, one-time instruction that creates the `FeeState` PDA (seeds = `["feestate"]`, a single deterministic address per program): [1](#0-0) 

The `Accounts` struct only requires an arbitrary `payer: Signer<'info>`; there is no check restricting who may call this instruction, nor any binding between the caller and the `admin_key` argument that gets written into `fee_state.global_fee_admin`: [2](#0-1) 

Because `fee_state` uses Anchor's `init` constraint on a fixed PDA, only the *first* transaction that lands wins — exactly analogous to the Fractional bug where only the first `init()` call on the shared `Vault` implementation succeeds. On Solana, a submitted-but-unconfirmed transaction is visible to the network before it lands, so an attacker can observe the legitimate deploy script's `init_global_fee_state` call and submit their own with a higher priority fee, taking the `admin_key`/`fee_wallet` slot for themselves before the real admin's transaction confirms.

Once initialized with attacker-controlled values, the `FeeState.global_fee_admin` cannot be recovered by anyone else — `edit_global_fee_state`/`edit_fee_state` requires `has_one = global_fee_admin`: [3](#0-2) 

and `panic_unpause` similarly gates on the same field: [4](#0-3) 

### Impact Explanation
`global_fee_admin` is a protocol-wide, singleton authority (per `guides/ADMIN/PERMISSIONS_AND_ROLES.md`, "FeeState - A global singleton account that stores protocol-level fee configuration and the global fee admin"). Whoever holds it can:
- Redirect all protocol fee revenue to an attacker-controlled `global_fee_wallet` via `edit_global_fee_state` (`edit_fee_state`, `programs/marginfi/src/instructions/marginfi_group/edit_global_fee.rs:10-106`).
- Arbitrarily raise `program_fee_fixed`/`program_fee_rate` charged to every group on the protocol.
- Control the panic/pause mechanism (`panic_unpause.rs`), potentially freezing or unfreezing the protocol at will, since `MarginfiGroupInitialize` and other flows check `is_protocol_paused()` sourced from this same fee-state pause data (`programs/marginfi/src/instructions/marginfi_group/initialize.rs:44-46`).
- Since the PDA is a singleton with a deterministic seed and no re-init path, once seized this is a durable, unrecoverable admin takeover requiring a full program migration/redeploy to fix — the same "no recovery path" severity dynamic as the referenced report.

This satisfies the "authorization bypass with financial effect" bar (fee revenue redirection + durable capture of a protocol-wide privileged role).

### Likelihood Explanation
This is a narrow, deployment-time race condition rather than an ongoing exploitable window: it only matters during the brief interval between program deployment and the legitimate `init_global_fee_state` transaction confirming on-chain. It requires an attacker to observe the pending transaction (e.g., via mempool/RPC monitoring) and win a fee/priority race. This is a real, previously-documented class of Solana initialization risk, but the exposure window is short and operationally could be mitigated (e.g., deploying with the init instruction bundled atomically, or using a known/hardcoded admin key), which is not currently done in this instruction's account constraints. Likelihood is Low-to-Medium; impact if it occurs is High.

### Recommendation
Add an explicit authorization check on `InitFeeState` (and `InitFeeStateV2`) rather than relying purely on "runs once" convention — e.g., require `payer` to match a hardcoded, protocol-controlled pubkey (or the program's upgrade authority fetched via `bpf_loader_upgradeable` program-data account), or bundle the fee-state creation with the very first `MarginfiGroupInitialize`/program-deploy transaction so no independent front-runnable window exists. At minimum, document and operationally enforce that `init_global_fee_state` must be executed atomically with deployment (e.g., in the same transaction as setting the program's final upgrade authority) to eliminate the race window.

### Proof of Concept
1. Program is deployed but `init_global_fee_state` has not yet been called (fresh PDA at `seeds=["feestate"]`).
2. Attacker constructs and signs an `init_global_fee_state` instruction: `payer = attacker`, `admin_key = attacker`, `fee_wallet = attacker_wallet`, with all other fee parameters set favorably (e.g., max fee rates payable to attacker), and submits it with high priority fee before the legitimate deploy script's equivalent transaction lands.
3. Because `fee_state` uses Anchor's `#[account(init, seeds=[FEE_STATE_SEED], ...)]` targeting a single deterministic PDA, the attacker's transaction — if confirmed first — succeeds and the legitimate deploy transaction subsequently fails ("account already in use").
4. `FeeState.global_fee_admin` and `global_fee_wallet` are now attacker-controlled permanently; attacker calls `edit_global_fee_state` to raise `program_fee_rate`/`program_fee_fixed` and redirect all future protocol fees to `global_fee_wallet` (`programs/marginfi/src/instructions/marginfi_group/edit_global_fee.rs`), and/or uses `panic_unpause`/pause-related fields to manipulate protocol pause state.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/init_global_fee_state.rs (L8-35)
```rust
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

**File:** programs/marginfi/src/instructions/marginfi_group/panic_unpause.rs (L39-51)
```rust
#[derive(Accounts)]
pub struct PanicUnpause<'info> {
    /// Global fee admin only.
    #[account(mut)]
    pub global_fee_admin: Signer<'info>,

    #[account(
        mut,
        seeds = [FEE_STATE_SEED.as_bytes()],
        bump,
        has_one = global_fee_admin @ MarginfiError::Unauthorized
    )]
    pub fee_state: AccountLoader<'info, FeeState>,
```
