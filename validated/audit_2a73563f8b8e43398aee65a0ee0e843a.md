### Title
`init_global_fee_state` allows front-running to seize the global fee admin role - ([File: programs/marginfi/src/instructions/marginfi_group/init_global_fee_state.rs])

### Summary
The `InitFeeState` account context and its handler `initialize_fee_state` create the singleton `FeeState` PDA (seeded solely by the constant `FEE_STATE_SEED`) and set `global_fee_admin` to whatever `admin_key` value the caller passes in. The instruction is guarded only by `#[account(init, ...)]` (i.e. "can only succeed once because the PDA doesn't exist yet") and requires nothing more than an arbitrary fee-paying `Signer`. This is the same root cause described in the external report for `PerpOwnable.transferPerpOwner`: a privileged, one-time "set the owner/admin" call intended to be executed once at deployment time, but with no restriction on *who* may call it, making it front-runnable by any observer of the mempool.

### Finding Description
`init_global_fee_state` is documented as "(Runs once per program)" [1](#0-0) , and its account struct derives the PDA from a fixed, program-wide constant seed with no additional discriminating input (such as a hard-coded deployer key or an `has_one` check against an existing authority): [2](#0-1) 

The handler itself blindly assigns the caller-supplied `admin_key` argument to `fee_state.global_fee_admin`: [3](#0-2) 

Because the `FeeState` PDA address is fully deterministic (`[FEE_STATE_SEED]`) and publicly known/derivable ahead of time, and because the instruction accepts any signer as `payer` with any `admin_key`/`fee_wallet` argument, an attacker monitoring the mempool for the legitimate deployer's `init_global_fee_state` transaction can submit their own transaction with a higher priority fee, causing Anchor's `init` constraint to succeed for the attacker first. Since the PDA is now initialized, the legitimate deployer's subsequent (or racing) transaction fails permanently (`init` requires the account not already exist), and the attacker's chosen `admin_key`/`fee_wallet` become permanently entrenched as `global_fee_admin` / `global_fee_wallet`.

This mirrors the report's root cause exactly: a function meant to run exactly once during setup, with the ownership value taken directly from an unauthenticated caller-supplied parameter, and no role/signer check tying execution to the deploying authority.

### Impact Explanation
Per `guides/ADMIN/PERMISSIONS_AND_ROLES.md`, the `global_fee_admin` role controls protocol-wide, financially significant state: [4](#0-3) 

Concretely, `EditFeeState` is gated only by `has_one = global_fee_admin` against the `FeeState` PDA that the attacker now controls: [5](#0-4) 

With control of `global_fee_admin`, an attacker can:
- Redirect the entire protocol's collected program fees to an attacker-controlled `global_fee_wallet` via `edit_global_fee_state` (value redirection with direct financial effect, since `fee_state_cache.global_fee_wallet` is propagated into every `MarginfiGroup`).
- Arbitrarily change `program_fee_fixed`/`program_fee_rate`, origination fees, and liquidation/order fee caps across the protocol.
- Set/clear the `pause_delegate_admin` and unilaterally `panic_pause` the entire protocol (denial of service across all groups/banks), matching the DoS impact class flagged in the analogous report.
- Since the PDA can never be re-initialized (Anchor `init` fails if the account exists), this is a **durable, unrecoverable** takeover unless a governance-level program upgrade intervenes — analogous to the "Denial of Service" and "renders the market useless" outcome described in the source report.

This is a genuine, unprivileged-attacker-reachable vulnerability with concrete financial impact (fee redirection) and protocol-wide DoS capability, not a validator/admin/theoretical-only scenario.

### Likelihood Explanation
Likelihood is tied to the deployment window: exploitation requires the attacker to observe the legitimate `init_global_fee_state` transaction in the mempool (or simply race to call it first, since the PDA address is fully derivable from the public program ID and constant seed — no observation of a pending tx is even strictly necessary, the attacker could call it proactively immediately after program deployment/upgrade). This is a narrow but real window (typically once per program deployment or fee-state migration), consistent with the "Low difficulty" rating given to the analogous `PerpOwnable` finding — the bug is trivial to exploit but only actionable at a specific point in time (initial deployment, or any redeployment/migration event that requires reinitializing the `FeeState` PDA).

### Recommendation
- Restrict `init_global_fee_state` to a hard-coded, compile-time-known deployer/multisig pubkey check (e.g., compare `ctx.accounts.payer.key()` against a constant `PROGRAM_UPGRADE_AUTHORITY`/governance pubkey), or require the transaction to be signed by the program's upgrade authority (verifiable via the `ProgramData` account), rather than trusting an arbitrary caller-supplied `admin_key`.
- Alternatively, perform the `FeeState` initialization atomically within the same transaction/instruction as program deployment/initial setup so there is no window for front-running.
- Add a regression test that asserts a non-designated signer cannot successfully call `init_global_fee_state`.
- Document this front-runnable initialization pattern (and any similar one-time `init` PDA instructions in the codebase) in the security documentation, per the report's long-term recommendation.

### Proof of Concept
1. Deployer publishes the marginfi program and prepares to call `init_global_fee_state(admin=deployer_pubkey, fee_wallet=deployer_treasury, ...)` targeting the deterministic PDA `[FEE_STATE_SEED]` [6](#0-5) .
2. Attacker independently derives the same PDA address from the public program ID and the constant seed (no insider knowledge needed), and/or observes the deployer's transaction in the mempool.
3. Attacker submits `init_global_fee_state(admin=attacker_pubkey, fee_wallet=attacker_wallet, ...)` with higher priority fee/compute unit price, landing first.
4. Anchor's `init` constraint succeeds for the attacker's transaction, permanently setting `fee_state.global_fee_admin = attacker_pubkey` and `fee_state.global_fee_wallet = attacker_wallet` [7](#0-6) .
5. The deployer's original transaction now fails (`FeeState` account already initialized).
6. Attacker subsequently calls `edit_global_fee_state` (authorized via `has_one = global_fee_admin`) to redirect fees, alter fee parameters, or call `panic_pause` to freeze the entire protocol at will [5](#0-4) .

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

**File:** programs/marginfi/src/instructions/marginfi_group/init_global_fee_state.rs (L8-24)
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

**File:** guides/ADMIN/PERMISSIONS_AND_ROLES.md (L112-126)
```markdown
## Global Fee Admin

The `global_fee_admin` is separate from the group-level admin roles. It is stored on the `FeeState`
account (a global singleton).

**Can do:**
- Edit global fee parameters (program fee rates, origination fee shares, init fees)
- Change the global fee wallet
- Set or clear the dedicated pause delegate admin
- Panic-pause the entire protocol (with rate limiting: max 4 consecutive pauses, max 3 per day,
  each lasting 6 hours)

This role is intended for the protocol operator (e.g. the foundation) and controls protocol-level
economics and emergency pause functionality.

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
