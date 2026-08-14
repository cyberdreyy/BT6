### Title
Permissionless front-runnable initialization of the global `FeeState` PDA allows attacker takeover of `global_fee_admin` - ([File: programs/marginfi/src/instructions/marginfi_group/init_global_fee_state.rs])

### Summary
The `init_global_fee_state` instruction creates the program's single global `FeeState` PDA and sets its `global_fee_admin` and `global_fee_wallet` fields directly from caller-supplied arguments, with no check that the caller is the program's upgrade authority or any other privileged party. Because the `FeeState` account is a deterministic PDA (`seeds = [FEE_STATE_SEED]`) and the instruction is meant to run "once per program" as a separate deployment step, this reproduces the exact bug class described in the external report: deployment and privileged initialization happen in separate transactions, so anyone can front-run the legitimate init call and seize the admin role on the canonical account address.

### Finding Description
`InitFeeState` requires only a funded `payer: Signer<'info>` and uses Anchor's `init` constraint against the PDA seeds `[FEE_STATE_SEED.as_bytes()]`: [1](#0-0) 

The handler sets `fee_state.global_fee_admin = admin_key` and `fee_state.global_fee_wallet = fee_wallet` directly from the caller-supplied arguments, with no validation against a hardcoded/expected admin, the program's upgrade authority, or any allow-list: [2](#0-1) 

The instruction is exposed publicly and explicitly documented as "Runs once per program", implying it is intended to be called in a deployment/setup script after the program is deployed, not atomically with deployment: [3](#0-2) 

Because the PDA address `[FEE_STATE_SEED]` is fully deterministic and known before the instruction is ever called, any party monitoring the chain for the marginfi program's deployment (or simply watching for its `programId`) can submit their own `init_global_fee_state` transaction with attacker-controlled `admin`/`fee_wallet` arguments before the legitimate operator's setup transaction lands. Since `init` fails if the account already exists, whoever's transaction confirms first permanently wins ownership of the singleton account — this is structurally identical to the reported EVM bug where a predictable contract address plus a separate, unauthenticated `init()` call allows front-running to steal ownership.

Once attacker-controlled, `global_fee_admin` gains authority over `edit_fee_state` (`EditFeeState`), which can further rewrite `global_fee_admin`, `global_fee_wallet`, and fee parameters at will: [4](#0-3) 

It can also redirect the `global_fee_wallet` referenced across the protocol's flat-fee flows (e.g. `PlaceOrder`, which validates `global_fee_wallet` against the `fee_state` PDA before transferring order-init fees to it): [5](#0-4) 

Additionally, `global_fee_admin` gates `panic_unpause`, giving the attacker control over the protocol's pause/unpause emergency mechanism: [6](#0-5) 

Note: this bug class does not extend to `marginfi_group_initialize` (`MarginfiGroupInitialize`), because `marginfi_group` there is a fresh, non-PDA `Keypair` account that must itself co-sign the init transaction — an attacker cannot squat that specific account address without possessing its private key, so front-running does not let them take over a *specific target group*; they can only create their own separate group. The `FeeState` PDA, by contrast, is a single deterministic, program-wide singleton with no such co-signing requirement, making it the valid analog.

### Impact Explanation
Taking over `global_fee_admin` grants control of the sole global fee state for the entire marginfi deployment: the attacker can redirect the `global_fee_wallet` that receives bank-init, liquidation, and order-execution flat fees and program fee revenue, arbitrarily adjust fee parameters, and control emergency pause/unpause. This is a durable, protocol-wide authorization bypass with direct financial-value-redirection impact (fee theft) and governance impact (illegitimate control of pause behavior), matching the "unauthorized state change / value redirection" bar for high-severity findings.

### Likelihood Explanation
Exploitation requires only that an attacker observe the marginfi program becoming live/upgraded (e.g., via a new deployment or an on-chain artifact revealing the program ID before `init_global_fee_state` is called) and race a single permissionless transaction referencing a fully deterministic PDA. No privileged keys, oracle manipulation, or complex preconditions are needed — this mirrors exactly the "monitor bytecode/deployment, front-run init" scenario in the source report. Likelihood is reduced only by operational practice (e.g., calling init atomically in the same transaction/bundle as deployment, or the fee state already existing on the live deployment), which is an operational mitigation, not a protocol-level guarantee enforced by the code itself.

### Recommendation
Add an authorization check to `InitFeeState` restricting the caller to a hardcoded/known deployer key or the program's upgrade authority (e.g., verify against `Program<System>`/`BpfLoaderUpgradeable` program-data account's `upgrade_authority_address`, or require a `#[account(constraint = payer.key() == EXPECTED_DEPLOYER)]`). Alternatively, ensure deployment tooling always bundles program deployment/upgrade and `init_global_fee_state` into the same atomic transaction so no window exists for front-running, and treat the instruction the same way privileged one-time setup instructions elsewhere in the codebase are protected.

### Proof of Concept
1. Attacker watches for the marginfi program's deployment (new `programId`) or for an on-chain announcement/upgrade of the program.
2. Before the legitimate operator submits their setup transaction calling `init_global_fee_state(admin=<team_admin>, fee_wallet=<team_wallet>, ...)`, the attacker submits their own transaction:
   - `payer = attacker_keypair`
   - `admin_key = attacker_pubkey`
   - `fee_wallet = attacker_pubkey`
   - accounts: `fee_state = PDA([FEE_STATE_SEED], program_id)` (deterministically computable by anyone, per `InitFeeState` account constraints: [7](#0-6) )
3. Because `fee_state` uses Anchor's `init` constraint, the first transaction to land succeeds and all subsequent calls (including the legitimate team's) fail with an "account already in use" error.
4. The attacker is now `global_fee_admin`; they call `edit_fee_state` to redirect `global_fee_wallet` to a wallet they control and/or call `panic_unpause` to bypass the emergency pause mechanism.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/init_global_fee_state.rs (L9-24)
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

**File:** programs/marginfi/src/instructions/marginfi_account/order.rs (L553-564)
```rust
    // Note: there is just one FeeState per program, so no further check is required.
    #[account(
        seeds = [FEE_STATE_SEED.as_bytes()],
        bump,
        has_one = global_fee_wallet @ MarginfiError::InvalidFeeWallet
    )]
    pub fee_state: AccountLoader<'info, FeeState>,

    /// CHECK: The fee admin's native SOL wallet, validated against fee state
    #[account(mut)]
    pub global_fee_wallet: UncheckedAccount<'info>,

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
