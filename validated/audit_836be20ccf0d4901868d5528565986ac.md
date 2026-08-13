### Title
Permissionless / frontrunnable `init_global_fee_state` allows attacker to seize the global fee admin role - (File: `programs/marginfi/src/instructions/marginfi_group/init_global_fee_state.rs`)

### Summary
The Algebra `initialize()` bug class (an unprotected "first caller sets the critical state" function that can be frontrun) has a stronger and more damaging analog in marginfi's `init_global_fee_state` instruction. Unlike a pool price (which self-corrects via arbitrage), this instruction permanently sets the **global fee admin and fee wallet for the entire program**, with no authorization check on who may call it.

### Finding Description
`init_global_fee_state` initializes the singleton `FeeState` PDA (seeded only by the constant `FEE_STATE_SEED`, i.e. one PDA per program, not per caller) and writes attacker-controlled arguments (`admin_key`, `fee_wallet`, and various fee parameters) directly into it: [1](#0-0) 

The account context enforces no privileged signer whatsoever — the only signer required is an arbitrary `payer`: [2](#0-1) 

The instruction is documented as "(Runs once per program)" in `lib.rs`, relying purely on Anchor's `init` constraint (first successful call wins) rather than any authorization check: [3](#0-2) 

Because the PDA seed is fixed and global (not tied to a specific deployer/multisig key), any unprivileged signer who submits this transaction first — including a bot racing the legitimate deployment transaction in the mempool, exactly the "frontrunning" scenario in the Algebra report — becomes the `global_fee_admin` and sets `global_fee_wallet` to an address of their choosing.

### Impact Explanation
`global_fee_admin` is the top-level privileged role that subsequently gates `edit_global_fee_state` (adjusting fees, admin, wallet, pause-delegate admin) and `config_group_fee` (enabling/disabling program fees for any group), as referenced by the access-control matrix in the repo's own docs (`Set fixed oracle price` / fee-state rows require `admin`, i.e., `global_fee_admin`): [4](#0-3) 

An attacker who wins this race gains permanent control of protocol-wide fee configuration and can redirect the `global_fee_wallet` that all groups' program fees flow to, i.e. a durable value-redirection / governance-takeover with direct financial effect on the entire deployed program, not just a single pool as in the Algebra case.

### Likelihood Explanation
This is a "runs once" instruction expected to be called during initial program setup/deployment. Because Solana transactions are broadcast through a public mempool before confirmation, and the instruction has zero authorization checks, any party monitoring for this call (or simply guessing it hasn't been called yet on a freshly deployed program) can submit a competing transaction with higher priority fee to land first, exactly mirroring the frontrunning mechanics described in the source report.

### Recommendation
Require the transaction to be signed/authorized by the actual upgrade authority or a hardcoded deploy-time admin key (e.g., check `payer.key() == UPGRADE_AUTHORITY` or compare against the program's upgradeable-loader `ProgramData` authority) before allowing `init_global_fee_state` to succeed, rather than trusting the first successful `init` call. Alternatively, bundle this initialization atomically within the deployment transaction so it cannot be raced independently.

### Proof of Concept
1. Program is deployed but `init_global_fee_state` has not yet been called (the `FeeState` PDA does not exist).
2. Attacker monitors the mempool/RPC for the deployer's `init_global_fee_state` transaction (or simply detects the PDA is uninitialized).
3. Attacker submits their own `init_global_fee_state` transaction with `admin_key = attacker_pubkey`, `fee_wallet = attacker_wallet`, and arbitrary fee parameters, with a higher priority fee so it lands first.
4. Anchor's `init` constraint on `fee_state` succeeds for whichever transaction lands first; the legitimate deployer's subsequent call fails because the PDA already exists.
5. Attacker is now `global_fee_admin` and can call `edit_global_fee_state`/`config_group_fee` to control protocol fee routing across all marginfi groups.

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

**File:** guides/ADMIN/PERMISSIONS_AND_ROLES.md (L249-270)
```markdown
## Access Control Matrix

| Instruction | Required Role |
|-------------|---------------|
| Configure group | `admin` |
| Add bank | `admin` |
| Configure bank (full) | `admin` |
| Configure bank oracle | `admin` |
| Set fixed oracle price | `admin` |
| Configure interest rate config | `admin` or `delegate_curve_admin` |
| Configure bank deposit/borrow/init limits | `admin` or `delegate_limit_admin` |
| Configure bank/group rate limits | `admin` or `delegate_limit_admin` |
| Configure deleverage withdraw daily limit | `admin` or `delegate_limit_admin` |
| Settle group rate limiter batches | `admin` or `delegate_limit_admin` |
| Settle deleverage withdraw batches | `admin` or `delegate_limit_admin` |
| Configure emissions | Deprecated / no-op (no active authority path) |
| Configure emode | `emode_admin` |
| Write bank metadata | `metadata_admin` |
| Freeze/unfreeze account | `admin` |
| Handle bankruptcy | `risk_admin` or `admin` (or permissionless if flag set) |
| Start forced deleverage | `risk_admin` |
| Force tokenless repay complete | `risk_admin` |
```
