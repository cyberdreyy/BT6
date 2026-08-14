### Title
Front-run of `init_global_fee_state` allows an unprivileged attacker to seize the global `FeeState` admin role - (File: programs/marginfi/src/instructions/marginfi_group/init_global_fee_state.rs)

### Summary
`init_global_fee_state` initializes the singleton `FeeState` PDA (`seeds = [FEE_STATE_SEED]`, one per program deployment) and directly stores an attacker-suppliable `admin_key` argument as `global_fee_admin`, with no constraint tying it to any specific/privileged signer. Any account can call this instruction with itself as `payer` and pass its own pubkey as `admin_key`. This is the exact "front-run initialize()" bug class from the report: whoever's transaction to call this permissionless, once-per-program instruction lands first wins control of the account, regardless of who deployed/upgraded the program.

### Finding Description
`InitFeeState` requires only a generic `Signer` `payer` and creates the `fee_state` PDA via `init`: [1](#0-0) 

The handler assigns the caller-supplied `admin_key` parameter straight into `fee_state.global_fee_admin` with no check that it matches the payer, an upgrade authority, or any pre-registered address: [2](#0-1) 

The instruction is exposed in the program's public entrypoint with no additional guard: [3](#0-2) 

Because `fee_state` is a PDA derived from a fixed seed (`FEE_STATE_SEED`), there is only one such account per program deployment, and Anchor's `init` constraint simply fails if the account already exists — it does not restrict *who* can be the first to succeed. This mirrors the EnsoWalletFactory issue: the vulnerable window is between program deployment/upgrade and the intended admin's `init_global_fee_state` call. During that window, any observer of the mempool/validator can submit their own transaction with themselves as `admin_key`, since deployment and initialization are not atomic and the instruction imposes no authorization on the caller.

Once an attacker becomes `global_fee_admin`, the following privileged actions on `FeeState` (identified via `has_one = global_fee_admin`) become theirs, per the documented Access Control Matrix: [4](#0-3) 
- `edit_global_fee_state` — change `global_fee_wallet` (redirect protocol fee revenue), change fee rates, and set/replace `global_fee_admin` and `pause_delegate_admin`: [5](#0-4) 
- `config_group_fee` — enable/disable program fees for any group: [6](#0-5) 
- Panic-pause the entire protocol.

### Impact Explanation
Gaining `global_fee_admin` lets the attacker:
- Redirect all protocol-level fees to an attacker-controlled wallet by editing `global_fee_wallet`.
- Set `program_fee_rate`/`program_fee_fixed` at will.
- Panic-pause the entire protocol, freezing normal user flows (deposit/withdraw/borrow/repay/etc. as blocked while paused per the permissions guide), a durable protocol-wide denial-of-service/griefing vector.
- Lock out the legitimate operator by reassigning `global_fee_admin`/`pause_delegate_admin` to itself, requiring a costly program upgrade to recover.

This has direct financial effect (fee redirection) and availability effect (protocol-wide pause), satisfying "authorization bypass, value redirection... or durable freeze/inconsistency with financial effect."

### Likelihood Explanation
`init_global_fee_state` is permissionless by design (any `Signer` can pay to create the PDA) and is meant to run exactly once per program (deployment/upgrade). The only defense is operational: whoever calls it first (in practice, in the same or immediately following transaction as deployment) wins. Any MEV searcher or bot monitoring for the constant `FEE_STATE_SEED` PDA becoming creatable (e.g., after a program upgrade or fresh deploy where the PDA does not yet exist) can front-run the legitimate initialization call, exactly as described in the reported bug class. Likelihood is realistic on any network with a public mempool/validator set (e.g., mainnet), especially since the deploy guides show initialization as a manual, separate step from deployment.

### Recommendation
- Require the `payer`/caller of `init_global_fee_state` (and `init_global_fee_state_v2`) to match a hardcoded/known privileged key (e.g., the program's upgrade authority, or a fixed governance/multisig pubkey compiled into the program via `declare_id!`-style constant or checked against `bpf_loader_upgradeable::UpgradeableLoaderState`), rather than accepting an arbitrary `admin_key` parameter from any signer.
- Alternatively, combine program deployment/upgrade and `FeeState` initialization into a single atomic transaction (e.g., call `init_global_fee_state` in the same upgrade transaction, or from an on-chain "first-deploy" trigger) so no window exists for a competing transaction to land first.
- Add a constraint such as `#[account(constraint = payer.key() == HARDCODED_ADMIN)]` or verify against the program's upgrade authority account to ensure only the legitimate deployer can set the initial `global_fee_admin`.

### Proof of Concept
1. Program is deployed/upgraded to mainnet; the `FeeState` PDA at `[FEE_STATE_SEED]` does not yet exist (e.g., fresh deploy or state was reset).
2. Attacker watches for this deployment and, before the legitimate admin sends its `init_global_fee_state` transaction, submits their own transaction calling `init_global_fee_state` with:
   - `payer` = attacker's own signer/wallet
   - `admin_key` = attacker's own pubkey
3. Anchor's `init` constraint succeeds because the PDA doesn't exist yet, so `fee_state.global_fee_admin` becomes attacker's pubkey.
4. The legitimate admin's subsequent `init_global_fee_state` transaction fails (`AccountAlreadyInUse`/init constraint), because the PDA already exists.
5. Attacker now calls `edit_global_fee_state` (which only checks `has_one = global_fee_admin`) to set `fee_wallet` to an attacker-controlled address and/or `panic_pause` the protocol, per: [5](#0-4)

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

**File:** programs/marginfi/src/lib.rs (L575-591)
```rust
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

**File:** programs/marginfi/src/instructions/marginfi_group/config_group_fee.rs (L8-23)
```rust
#[derive(Accounts)]
pub struct ConfigGroupFee<'info> {
    #[account(mut)]
    pub marginfi_group: AccountLoader<'info, MarginfiGroup>,

    /// `global_fee_admin` of the FeeState
    pub global_fee_admin: Signer<'info>,

    // Note: there is just one FeeState per program, so no further check is required.
    #[account(
        seeds = [FEE_STATE_SEED.as_bytes()],
        bump,
        has_one = global_fee_admin @ MarginfiError::Unauthorized
    )]
    pub fee_state: AccountLoader<'info, FeeState>,
}
```
