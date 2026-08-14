### Title
Unpermissioned one-time `init_global_fee_state` allows front-running to seize `global_fee_admin` - (File: programs/marginfi/src/instructions/marginfi_group/init_global_fee_state.rs)

### Summary
`init_global_fee_state` initializes the program-wide `FeeState` PDA (seed `FEE_STATE_SEED`, i.e. one fixed, deterministic address for the whole program) and lets the caller supply an arbitrary `admin_key`/`fee_wallet` with no signer-authorization check tying the call to a legitimate deployer. This is the same class of bug as the EVM report: an `initialize()`-style function that establishes a privileged admin role is not permissioned and not guaranteed to be executed atomically with deployment, so whoever's transaction lands first claims the admin role.

### Finding Description
`InitFeeState` requires only a generic `payer: Signer` and creates the `fee_state` PDA via `init` (fails only if the PDA already exists): [1](#0-0) 

The handler sets `fee_state.global_fee_admin = admin_key` and `fee_state.global_fee_wallet = fee_wallet` directly from caller-supplied arguments, with no `has_one`/authority check restricting who may call it or which `admin_key`/`fee_wallet` may be set: [2](#0-1) 

The exposed program entrypoint documents this as "Runs once per program", confirming the intended flow is a single, unrepeatable initialization — exactly the kind of one-shot init call that the reported bug class targets: [3](#0-2) 

Because `FEE_STATE_SEED` is a fixed, program-wide constant (no group- or deployer-specific component), the PDA address is fully deterministic and known to anyone before the legitimate deployer's transaction lands. Since Solana transactions are visible in the mempool before confirmation, an attacker can observe the deployer's `InitFeeState` transaction (or independently guess it needs to happen) and submit their own `init_global_fee_state` call with a higher priority fee, using `Pubkey::find_program_address(&[FEE_STATE_SEED.as_bytes()], &marginfi::ID)` for the same fixed address. Whichever transaction's `init` succeeds first captures the `global_fee_admin` and `global_fee_wallet` roles for the entire deployment permanently (since only `has_one`-gated `edit_global_fee_state`, restricted to the current `global_fee_admin`, can change it thereafter): [4](#0-3) 

This mirrors the report's root cause precisely: an unpermissioned, one-time "initialize" call whose caller becomes the permanent administrator, deployed via a script/flow that is not guaranteed to be atomic (unlike, e.g., `MarginfiGroupInitialize`, which is safe from this class of attack because the group account is a fresh `Keypair` that must co-sign the same transaction as the payer, so an attacker cannot pre-empt it without possessing that keypair — see the contrast at [5](#0-4) ).

### Impact Explanation
Whoever wins the race to call `init_global_fee_state` becomes the permanent `global_fee_admin` of the entire marginfi deployment (until/unless the legitimate admin somehow regains control, which is not possible once `has_one` is locked to the attacker's key). This grants:
- Control over `global_fee_wallet`, redirecting all protocol-level fees (`program_fee_fixed`/`program_fee_rate` collected via `lending_pool_collect_bank_fees`, which validates the destination ATA against `fee_state.global_fee_wallet`) to an attacker-controlled wallet — see [6](#0-5) .
- `is_pause_authority` privileges (`panic_pause`/`panic_unpause`), letting the attacker as `global_fee_admin` pause/unpause protocol-wide panic state at will — see [7](#0-6)  and [8](#0-7) .
- Ability to set arbitrary fee parameters and a `pause_delegate_admin` via `edit_global_fee_state` once they own `global_fee_admin`.

This constitutes value redirection (fee siphoning) and unauthorized/durable state capture with financial effect at the whole-protocol level, not scoped to a single group or bank.

### Likelihood Explanation
Likelihood depends entirely on the operational deployment procedure, which is outside the on-chain code shown here: if the deployer always submits `init_global_fee_state` and any dependent transactions atomically (single transaction, or via a permissioned bootstrap flow), the window doesn't exist. However, the on-chain instruction itself provides no such guarantee or protection (no admin-only gate, no requirement that it be co-signed by a hardcoded upgrade authority, no check that the payer is the program's upgrade authority), so protection is purely a matter of off-chain deployment discipline — the exact failure mode called out in the original report ("task scripts... do not utilize... Hardhat code to deploy and initialize in the same transaction"). Given this is a one-time, whole-program action typically performed once during initial mainnet/devnet rollout (and potentially repeated on redeployments/test environments), the exposure window is narrow but real, and the PDA address is fully predictable in advance, making it a targeted, low-cost front-running opportunity for anyone monitoring the deployer's known upgrade-authority address or program ID for the first `init_global_fee_state` call.

### Recommendation
- Gate `init_global_fee_state` so only the program's upgrade authority (or another hardcoded/config-derived key) can call it, e.g. add a constraint comparing `payer.key()` (or a dedicated signer) against `bpf_loader_upgradeable`'s stored upgrade authority for this program, similar to patterns used elsewhere in the codebase for admin-only instructions.
- Alternatively/additionally, require this instruction be invoked from a deploy script that bundles it into the same transaction as the program's final upgrade/initialization step, removing any window between deployability and initialization.
- Monitor the `FEE_STATE_SEED` PDA immediately after any deployment/upgrade and verify `global_fee_admin`/`global_fee_wallet` match expected values before proceeding with any other setup that depends on `FeeState`.

### Proof of Concept
1. Compute the deterministic fee-state PDA off-chain: `Pubkey::find_program_address(&[FEE_STATE_SEED.as_bytes()], &marginfi::ID)` (same derivation used by the legitimate deployer, shown in [9](#0-8) ).
2. Before (or racing) the legitimate deployer's `init_global_fee_state` transaction is confirmed, submit a transaction calling `init_global_fee_state(attacker_admin, attacker_wallet, ...)` with a higher priority fee, using the same PDA and any `payer` signer (does not need to be the real deployer).
3. If the attacker's transaction lands first, `InitFeeState`'s `init` constraint succeeds (account did not previously exist) and `fee_state.global_fee_admin`/`global_fee_wallet` are permanently set per [10](#0-9) .
4. The legitimate deployer's subsequent `init_global_fee_state` call now fails (`already in use`), and all future `edit_global_fee_state` calls require `has_one = global_fee_admin` matching the attacker's key, permanently locking out the real admin and redirecting protocol fee flow and pause authority to the attacker.

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

**File:** programs/marginfi/src/instructions/marginfi_group/initialize.rs (L53-72)
```rust
#[derive(Accounts)]
pub struct MarginfiGroupInitialize<'info> {
    #[account(
        init,
        payer = admin,
        space = 8 + std::mem::size_of::<MarginfiGroup>(),
    )]
    pub marginfi_group: AccountLoader<'info, MarginfiGroup>,

    #[account(mut)]
    pub admin: Signer<'info>,

    #[account(
        seeds = [FEE_STATE_SEED.as_bytes()],
        bump,
    )]
    pub fee_state: AccountLoader<'info, FeeState>,

    pub system_program: Program<'info, System>,
}
```

**File:** programs/marginfi/src/instructions/marginfi_group/collect_bank_fees.rs (L26-38)
```rust
    // Validate the program fee ata is correct
    {
        let mint = &bank.mint;
        let global_fee_wallet = &ctx.accounts.fee_state.load()?.global_fee_wallet;
        let token_program_id = &ctx.accounts.token_program.key();
        let program_fee_ata = &ctx.accounts.fee_ata.key();
        let ata_expected =
            get_associated_token_address_with_program_id(global_fee_wallet, mint, token_program_id);
        check!(
            program_fee_ata.eq(&ata_expected),
            MarginfiError::InvalidFeeAta
        );
    }
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

**File:** programs/marginfi/src/instructions/marginfi_group/panic_pause.rs (L33-46)
```rust
#[derive(Accounts)]
pub struct PanicPause<'info> {
    /// Global fee admin or the dedicated pause delegate admin.
    pub pause_authority: Signer<'info>,

    /// Global fee state account containing the panic state
    #[account(
        mut,
        seeds = [FEE_STATE_SEED.as_bytes()],
        bump,
        constraint = fee_state.load()?.is_pause_authority(pause_authority.key()) @ MarginfiError::Unauthorized
    )]
    pub fee_state: AccountLoader<'info, FeeState>,
}
```

**File:** test-utils/src/marginfi_group.rs (L56-63)
```rust
        let group_key = Keypair::new();
        let fee_wallet_key: Pubkey;
        let (fee_state_key, _bump) =
            Pubkey::find_program_address(&[FEE_STATE_SEED.as_bytes()], &marginfi::ID);
        let (staked_settings_key, _bump) = Pubkey::find_program_address(
            &[STAKED_SETTINGS_SEED.as_bytes(), group_key.pubkey().as_ref()],
            &marginfi::ID,
        );
```
