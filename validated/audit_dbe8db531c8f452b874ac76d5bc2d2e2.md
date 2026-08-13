### Title
Unauthenticated first-caller of `initialize_fee_state` can seize permanent `global_fee_admin`/`global_fee_wallet` control - ([File: programs/marginfi/src/instructions/marginfi_group/init_global_fee_state.rs])

### Summary
`initialize_fee_state` initializes the singleton `FeeState` PDA and sets `global_fee_admin`/`global_fee_wallet` directly from caller-supplied arguments, with the `InitFeeState` accounts struct requiring only an arbitrary `payer: Signer<'info>` — there is no check that `payer`, `admin_key`, or `fee_wallet` matches any hardcoded, upgrade-authority, or governance-controlled pubkey. Because the PDA is `init`-only (non-reinitializable) and derived solely from `FEE_STATE_SEED`, whoever's transaction lands first permanently owns the fee-admin role.

### Finding Description
`initialize_fee_state` blindly assigns `fee_state.global_fee_admin = admin_key` and `fee_state.global_fee_wallet = fee_wallet` from arbitrary instruction arguments: [1](#0-0) 

The associated `InitFeeState` accounts struct only requires a generic `Signer` as payer and initializes the PDA via `init`/`seeds = [FEE_STATE_SEED.as_bytes()]`/`bump`, with no authority constraint on who may call it: [2](#0-1) 

Downstream privileged instructions such as `edit_fee_state` and `panic_unpause` rely entirely on `has_one = global_fee_admin` against whatever was set at init time, with no fallback authority: [3](#0-2) [4](#0-3) 

Since the PDA is seeded only by the constant `FEE_STATE_SEED` and created with Anchor's `init` (which fails if the account already exists), there is no re-initialization path once occupied. An unprivileged attacker monitoring the mempool/program deployment can submit `initialize_fee_state` with their own `admin_key`/`fee_wallet` before the legitimate deployer's transaction executes, permanently capturing the admin role with no on-chain remedy.

### Impact Explanation
Capturing `global_fee_admin` grants the attacker permanent control over `edit_fee_state` (able to redirect `global_fee_wallet`, alter `program_fee_fixed`/`program_fee_rate`, liquidation/order fee caps, and `pause_delegate_admin`) and `panic_unpause` (protocol pause/unpause control), causing durable protocol inconsistency and fee-flow hijack with no admin-side fix, since the FeeState PDA cannot be reinitialized. This matches the "protocol inconsistency … without an on-chain admin fix path" scope for a Medium-severity finding.

### Likelihood Explanation
This is only exploitable in the narrow window between program deployment and the legitimate first call to `initialize_fee_state` (a one-time bootstrap instruction). It requires the attacker to race/front-run the deployer's initialization transaction on a freshly deployed program instance, which is feasible but time-limited and operationally avoidable (e.g., deployer initializing atomically within the same transaction/bundle as deployment, or via a private RPC). It is not exploitable against an already-initialized `FeeState`.

### Recommendation
Restrict `InitFeeState.payer` (or add an explicit authority account) to a hardcoded pubkey (e.g., the program's upgrade authority) checked via an `Anchor` constraint, or require the instruction to be invoked atomically as part of the deployment transaction/multisig bundle so no independent front-running window exists. Alternatively, gate the instruction behind a one-time governance-controlled flag validated at the program level.

### Proof of Concept
Integration test (bankrun/anchor test) plan:
1. Deploy the program fresh (or reset the `FeeState` PDA state) without calling `initialize_fee_state`.
2. Have an "attacker" keypair (not the deployer) submit `initialize_fee_state(ctx, attacker_admin, attacker_wallet, ...)` as `payer`.
3. Assert the transaction succeeds and `FeeState.global_fee_admin == attacker_admin` / `global_fee_wallet == attacker_wallet`.
4. Have the legitimate deployer subsequently attempt `initialize_fee_state` with their intended admin — assert it fails with an "account already in use" error (Anchor `init` constraint), proving no recovery path.
5. Have the attacker call `edit_fee_state`/`panic_unpause` using `attacker_admin` as signer and assert success, demonstrating full admin takeover.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/init_global_fee_state.rs (L20-24)
```rust
) -> Result<()> {
    let mut fee_state = ctx.accounts.fee_state.load_init()?;
    fee_state.global_fee_admin = admin_key;
    fee_state.global_fee_wallet = fee_wallet;
    fee_state.key = ctx.accounts.fee_state.key();
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

**File:** programs/marginfi/src/instructions/marginfi_group/panic_unpause.rs (L39-52)
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
}
```
