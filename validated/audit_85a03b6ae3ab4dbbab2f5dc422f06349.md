### Title
Unauthenticated `initialize_fee_state` allows any signer to front-run global fee configuration and permanently zero protocol fees - ([File: programs/marginfi/src/instructions/marginfi_group/init_global_fee_state.rs])

### Summary
`InitFeeState` only requires a generic `Signer<'info>` as `payer` with no check against any designated deployer/admin pubkey, and the `fee_state` PDA is derived from the constant seed `FEE_STATE_SEED` alone, with no per-caller component. Because Anchor's `init` constraint only guarantees the account doesn't already exist (not that the caller is privileged), any unprivileged signer who submits the transaction first can set arbitrary `admin_key`, `fee_wallet`, and zero/attacker-favorable fee parameters, permanently occupying the singleton PDA.

### Finding Description
The account struct is: [1](#0-0) 

`payer` is a bare `Signer<'info>` — there is no `has_one`, hardcoded pubkey constraint, or comparison against any program-baked admin authority. The `fee_state` PDA seeds are `[FEE_STATE_SEED.as_bytes()]` only [2](#0-1) , i.e. a fixed, globally-unique address with no caller-specific component, so whichever transaction lands first "wins" the account via Anchor's `init` (which just checks the account is currently unallocated).

Inside `initialize_fee_state`, the caller-supplied `admin_key`, `fee_wallet`, and all fee parameters (`program_fee_rate`, `program_fee_fixed`, `liquidation_max_fee`, `bank_init_flat_sol_fee`, etc.) are written verbatim into the newly created `FeeState` account with no validation: [3](#0-2) .

By contrast, the update path `EditFeeState` explicitly enforces `has_one = global_fee_admin @ MarginfiError::Unauthorized` against the `fee_state.global_fee_admin` field that was set at init time [4](#0-3) . This proves the protocol's intended model is "whoever sets `global_fee_admin` at init controls all future edits" — but nothing gates who is allowed to perform that initial write. Since `FEE_STATE_SEED` is a fixed, singleton seed with no salt, and `init` only succeeds once, there is no re-initialization path if an attacker (or even a benign but wrong actor) front-runs the legitimate deployer.

### Impact Explanation
An unprivileged attacker who races the legitimate deployment (e.g., by monitoring the mempool/program deployment and submitting `initialize_fee_state` first) can become the permanent `global_fee_admin`, and/or set `program_fee_rate=0`, `program_fee_fixed=0`, `liquidation_max_fee=0`, and `bank_init_flat_sol_fee=0`. Because the PDA is a program-wide singleton and there is no re-init instruction, this zeroed/attacker-controlled state is durable — the real admin cannot recreate the account, and can only regain control via `EditFeeState`, which itself requires knowing/holding the current `global_fee_admin` signer (attacker-controlled) unless the attacker is cooperative or the attacker's key is compromised/replaced through some other governance action outside the scope of this instruction. This results in permanent griefing of protocol fee revenue (`program_fee_rate`/`program_fee_fixed` collection), matching the scoped impact described.

### Likelihood Explanation
The only precondition is an unfunded keypair with enough SOL for rent to pay for `fee_state` account creation — no privileged role, leaked key, or governance access is needed. This is a permissionless, one-time race against the legitimate deployment transaction, making it feasible for any observer of the deploy sequence (e.g., via mempool monitoring or simply calling it before the real admin does on a freshly deployed program) to win the race deterministically.

### Recommendation
Add an authorization check to `InitFeeState`, e.g., require `payer` to match a hardcoded/program-upgrade-authority pubkey, or gate the instruction so it can only be invoked once by the program's upgrade authority (checked via `BpfLoaderUpgradeable` program data account), rather than accepting an arbitrary `Signer`.

### Proof of Concept
Rust integration test plan:
1. Deploy the program in a local test validator/Bankrun environment without ever calling `initialize_fee_state`.
2. Construct an `InitFeeState` context where `payer` is a freshly generated, unfunded-but-airdropped `Keypair` unrelated to any designated admin/deployer key.
3. Call `initialize_fee_state(ctx, attacker_admin_key, attacker_fee_wallet, 0, 0, 0, WrappedI80F48::zero(), WrappedI80F48::zero(), WrappedI80F48::zero(), WrappedI80F48::zero())`.
4. Assert the transaction succeeds (no `Unauthorized`/`ConstraintSigner` error), and that the resulting `FeeState` account's `global_fee_admin == attacker_admin_key`, `program_fee_rate == 0`, `program_fee_fixed == 0`.
5. Attempt to call `initialize_fee_state` a second time with the legitimate admin key and assert it fails with `already in use` (confirming no re-init path exists), demonstrating the permanent lock-in.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/init_global_fee_state.rs (L20-34)
```rust
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
