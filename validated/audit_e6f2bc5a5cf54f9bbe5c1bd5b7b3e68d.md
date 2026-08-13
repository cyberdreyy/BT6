### Title
`init_global_fee_state` (global fee-state singleton) initializer can be front-run by any unprivileged signer - (File: `programs/marginfi/src/instructions/marginfi_group/init_global_fee_state.rs`)

### Summary
The `InitFeeState` instruction creates the single global `FeeState` PDA (seeded only by `FEE_STATE_SEED`, with no group or admin-specific seed component) and lets the caller supply the values that will become the permanent `global_fee_admin` and `global_fee_wallet`. The only account constraint is that `payer` be *a* signer — there is no check that the caller is the program's upgrade authority or any other pre-agreed trusted key. [1](#0-0) [2](#0-1) 

### Finding Description
`InitFeeState::fee_state` is a PDA derived purely from the constant seed `FEE_STATE_SEED`, so its address is fully deterministic and known before deployment/initialization occurs. [3](#0-2) 

The handler, `initialize_fee_state`, blindly writes the caller-supplied `admin_key` and `fee_wallet` parameters into the newly created account with no validation against a hardcoded or otherwise privileged address: [4](#0-3) 

This is architecturally identical to the reported `gorples-bridge` `Initialize` bug: a one-time, security-critical config account whose initializer instruction is only gated by "any signer," allowing an attacker to race the legitimate deployer's transaction on public mempool/RPC and claim the singleton PDA with attacker-controlled `admin_key`/`fee_wallet` values.

Once the account is initialized (whether by the legitimate deployer or an attacker), the docs confirm that only the resulting `global_fee_admin` can subsequently edit the fee state or manage protocol-wide pause/fee behavior — there is no reset/ownership-transfer mechanism outside calling `edit_global_fee_state`, which itself requires `global_fee_admin` authorization (i.e., the compromised key): [5](#0-4) 

### Impact Explanation
Whoever initializes `FeeState` becomes `global_fee_admin`, an account with protocol-wide authority: control over `global_fee_wallet` (destination of program fees collected across every group/bank system-wide), the ability to edit the global fee state, set the pause-delegate admin, and to panic-pause/unpause the entire protocol. If an attacker front-runs `InitFeeState`, they can redirect all future protocol fee income to their own wallet and/or hold global pause authority over marginfi, a durable and protocol-wide compromise with direct financial impact once real banks and groups start collecting fees.

### Likelihood Explanation
The `FeeState` PDA address is deterministic and public (single fixed seed), so an attacker monitoring the mempool/RPC for the program's first deployment transactions can trivially front-run the legitimate `InitFeeState` call with their own transaction containing the same account and attacker-chosen `admin_key`/`fee_wallet` parameters, since no authorization check restricts the caller. This requires no special access beyond normal transaction submission (unprivileged-user analog), matching the exact bug class described in the report.

### Recommendation
Restrict `InitFeeState`/`initialize_fee_state` so it can only be executed by a known, trusted key — e.g., require the signer to match the program's upgrade authority (validated via the `BpfLoaderUpgradeable` `ProgramData` account), or hardcode the expected deployer/admin pubkey and assert against it in the account constraints, mirroring the fix applied to the `gorples-bridge` program (hardcoded deployer key check). Additionally, consider adding an explicit ownership-transfer mechanism for `global_fee_admin` so a compromised or incorrectly-initialized fee state can be recovered by a higher authority.

### Proof of Concept
1. Attacker observes the marginfi program deployment (program ID is known/public) before the legitimate `InitFeeState` transaction is confirmed.
2. Attacker derives the deterministic `fee_state` PDA using `[FEE_STATE_SEED]` and submits their own `InitFeeState` transaction first, supplying `admin_key = attacker`, `fee_wallet = attacker_controlled_wallet`.
3. The instruction succeeds because the accounts struct only requires `payer: Signer<'info>` — no allow-list or upgrade-authority check is present: [6](#0-5) 
4. `fee_state.global_fee_admin` and `fee_state.global_fee_wallet` are now permanently attacker-controlled, and the legitimate deployer's subsequent `InitFeeState` call fails (`init` on an already-initialized PDA), leaving the protocol's global fee configuration under attacker control indefinitely.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/init_global_fee_state.rs (L8-34)
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

**File:** guides/ADMIN/PERMISSIONS_AND_ROLES.md (L271-276)
```markdown
| Edit global fee state | `global_fee_admin` |
| Set pause delegate admin | `global_fee_admin` |
| Panic-pause protocol | `global_fee_admin` or `pause_delegate_admin` |
| Unpause protocol (early) | `global_fee_admin` or `pause_delegate_admin` |
| Unpause protocol (after expiry) | Anyone |
| Forced deleverage during pause | `risk_admin` |
```
