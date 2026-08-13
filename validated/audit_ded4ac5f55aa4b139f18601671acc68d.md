### Title
Permissionless `init_global_fee_state` allows front-running the deploy to seize global fee admin and fee wallet - (File: `programs/marginfi/src/instructions/marginfi_group/init_global_fee_state.rs`)

### Summary
The `init_global_fee_state` instruction initializes the program-wide `FeeState` singleton PDA and accepts attacker-controlled `admin_key`/`fee_wallet` parameters from any caller, with no check that the caller is the legitimate deployer. Because the PDA seed is a fixed global constant (`FEE_STATE_SEED`), whoever's transaction lands first permanently claims the `global_fee_admin` and `global_fee_wallet` roles for the entire protocol.

### Finding Description
`InitFeeState` only requires an arbitrary `Signer` as `payer`; there is no admin/deployer authorization check on who can call `init_global_fee_state`: [1](#0-0) 

The handler blindly writes the caller-supplied `admin_key` and `fee_wallet` into the newly created `fee_state`: [2](#0-1) 

The `fee_state` account is a PDA derived solely from `FEE_STATE_SEED` (no per-group or per-caller component), i.e. a single global singleton for the entire program: [3](#0-2) 

Since the account uses Anchor's `init` constraint, only the first successful call can ever create it — any subsequent call to `init_global_fee_state` fails because the account already exists. This is the exact permissionless-init race condition described in the external report: the vulnerable function can be called by anyone, and whoever wins the race becomes entrenched.

Once set, `global_fee_admin` is the sole authority that can change `global_fee_admin`/`global_fee_wallet` later via `edit_global_fee_state`, which is gated only by `has_one = global_fee_admin`: [4](#0-3) 

So an attacker who wins the initialization race becomes the permanent, unremovable `global_fee_admin` (no other privileged path resets `FeeState`), and simultaneously sets `global_fee_wallet` to an address of their choosing.

### Impact Explanation
`global_fee_wallet` is the recipient of protocol-wide fees (bank-init flat SOL fees, liquidation flat fees, order-init flat fees, and ongoing program fee skims cached into every `MarginfiGroup` via `fee_state_cache` at `marginfi_group_initialize`): [5](#0-4) 

If an attacker front-runs the legitimate `init_global_fee_state` call, all subsequent protocol fee revenue is misdirected to the attacker's wallet, and the attacker also holds `global_fee_admin`, granting further ability to edit fees/wallets, and (per `is_pause_authority`) act as a pause authority: [6](#0-5) 

This is a concrete authorization bypass and value redirection with durable financial effect (permanent fee-wallet hijack), not merely a gas-griefing / redeploy-cost issue.

### Likelihood Explanation
Likelihood is low-to-moderate: the attacker must observe the deployment transaction (or predict the deploy sequence) and submit a competing `init_global_fee_state` transaction before the legitimate deployer's transaction lands, similar to the "narrow race window at deploy time" scenario judged Medium in the referenced report. On Solana this is somewhat easier than on typical EVM deployments because the instruction can be sent as soon as the program is deployed/upgraded and the PDA is derivable in advance from the known program ID and constant seed, without needing any other privileged account.

### Recommendation
Add an explicit authorization check to `InitFeeState`/`init_global_fee_state`, e.g., require the `payer`/caller to match a hardcoded upgrade authority or an already-established admin pubkey (similar to how `EditFeeState` requires `has_one = global_fee_admin`), or perform the initialization atomically within the same transaction/instruction as program deployment/upgrade so it cannot be front-run.

### Proof of Concept
1. Deployer deploys/upgrades the `marginfi` program.
2. Attacker observes the deployment and, before the deployer's setup script executes, submits a transaction calling `init_global_fee_state(admin = attacker_pubkey, fee_wallet = attacker_pubkey, ...)` using the well-known `FEE_STATE_SEED` PDA derivation: [1](#0-0) 
3. Because `fee_state` uses `init` (create-once) semantics, the attacker's transaction succeeds and permanently sets `fee_state.global_fee_admin` and `fee_state.global_fee_wallet` to attacker-controlled keys.
4. The legitimate deployer's subsequent `init_global_fee_state` call fails (account already initialized); all future protocol fees route to the attacker's `fee_wallet`, and only the attacker (as `global_fee_admin`) can ever change it via `edit_global_fee_state`.

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

**File:** programs/marginfi/src/instructions/marginfi_group/initialize.rs (L30-32)
```rust
    marginfi_group.fee_state_cache.global_fee_wallet = fee_state.global_fee_wallet;
    marginfi_group.fee_state_cache.program_fee_fixed = fee_state.program_fee_fixed;
    marginfi_group.fee_state_cache.program_fee_rate = fee_state.program_fee_rate;
```

**File:** programs/marginfi/src/state/fee_state.rs (L9-15)
```rust
impl FeeStateImpl for FeeState {
    fn is_pause_authority(&self, signer: Pubkey) -> bool {
        signer == self.global_fee_admin
            || (self.pause_delegate_admin != Pubkey::default()
                && signer == self.pause_delegate_admin)
    }
}
```
