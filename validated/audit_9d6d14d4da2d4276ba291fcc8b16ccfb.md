### Title
Missing Zero-Address Validation on `global_fee_admin`/Group Admin Rotation Enables Permanent Bricking of Privileged Roles - (File: `programs/marginfi/src/instructions/marginfi_group/edit_global_fee.rs`, `programs/marginfi/src/instructions/marginfi_group/configure.rs`)

### Summary
The `boosterMultiplier`/`pointsPerBlock` finding in the external report is really about two related issues: (1) unbounded numeric owner-controlled parameters that can overflow accounting, and (2) missing zero-address validation on owner-controlled address parameters. In marginfi, bounded numeric config fields (bank weights, interest-rate curve points, emode leverage) are validated via `InterestRateConfigImpl::validate`/`validate_seven_point` [1](#0-0)  and `validate_and_apply_emode_leverage` [2](#0-1) , so the numeric-overflow analog is largely mitigated. However, the zero-address-validation half of the report is a real, reachable analog: privileged address fields such as `global_fee_admin` and the marginfi group `admin` can be rotated to `Pubkey::default()` (the system-owned zero address) with no validation.

### Finding Description
`edit_fee_state` lets the current `global_fee_admin` set a brand-new `admin` pubkey for the singleton `FeeState` account with no non-zero check: [3](#0-2) 

The `FeeState.global_fee_admin` field is then enforced purely via Anchor's `has_one = global_fee_admin` constraint in the `EditFeeState` accounts struct: [4](#0-3) 

Because `global_fee_admin` is a plain `Signer<'info>` compared against a stored `Pubkey`, if that stored pubkey is ever set to `Pubkey::default()` (the all-zero System Program address), no future transaction can satisfy the `has_one` check — nobody holds (or can hold) the private key for the zero address. This permanently locks out the `global_fee_admin` role from every instruction gated on it (`edit_global_fee_state`, `set_pause_delegate_admin` via the same instruction, and by extension the pause/unpause admin chain described in `guides/ADMIN/PERMISSIONS_AND_ROLES.md`, which states the pause-delegate role "can pause (not unpause) the protocol" — implying unpause authority ultimately traces back to `global_fee_admin`).

The identical pattern exists at the group level: `configure()` lets the current group `admin` rotate `new_admin`, `new_emode_admin`, `new_curve_admin`, `new_limit_admin`, `new_flow_admin`, `new_metadata_admin`, and `new_risk_admin` to arbitrary pubkeys, all unchecked for zero: [5](#0-4)  enforced by `has_one = admin` on `MarginfiGroupConfigure`: [6](#0-5) 

Setting `new_admin` to the zero address bricks all future group-admin actions (bank configuration, bank pausing, closing banks, fee withdrawal) for that group permanently, since `has_one = admin` can never again be satisfied by a real signer.

### Impact Explanation
If the `global_fee_admin` field is bricked to the zero address (accidentally, via a compromised admin key executing a mistaken/malicious transaction, or via a UI bug that defaults an unset field to `Pubkey::default()` instead of `None`), the protocol permanently loses the ability to: adjust global fee parameters, rotate the fee wallet, and manage the `pause_delegate_admin`/pause-unpause chain. If the protocol is paused at the time (or becomes paused later, e.g., via `panic_pause`), it can never be unpaused, freezing user deposits/withdrawals indefinitely — a durable, protocol-wide financial-impact freeze. The group-level analog (`configure`) similarly bricks a specific group's admin functions permanently (banks can't be reconfigured, paused, or have fees collected), which is a durable freeze scoped to that group's markets.

### Likelihood Explanation
This requires the current `global_fee_admin` or group `admin` (a privileged signer) to submit a transaction that sets the new admin field to `Pubkey::default()`. This is not attacker-controlled without already compromising or being the current admin, but the on-chain program provides zero defense-in-depth against operational error (e.g., a script/CLI passing an uninitialized/zeroed `Pubkey` due to a bug, or `None`-vs-`Pubkey::default()` confusion, as hinted by the `p0-cli` tooling that constructs these instructions). Given the report explicitly flags "lack of input validation for the zero address in owner-controlled functions" as a real, client-acknowledged bug class in the reference codebase, and marginfi has no equivalent guard on these two admin-rotation paths, the likelihood of an eventual operational mistake causing this is non-trivial, especially since there is no on-chain safety net (e.g., a two-step admin transfer/accept pattern) to recover from it.

### Recommendation
Add an explicit check that rejects `Pubkey::default()` for `admin`, `global_fee_admin`, and all other admin/role fields in `edit_fee_state` (`programs/marginfi/src/instructions/marginfi_group/edit_global_fee.rs`) and `configure` (`programs/marginfi/src/instructions/marginfi_group/configure.rs`). Consider adopting a two-step "propose/accept" ownership-transfer pattern for `global_fee_admin` and group `admin` so that a typo or zeroed pubkey cannot immediately and irrevocably brick the role, mirroring standard `Ownable2Step`-style protections.

### Proof of Concept
1. Current `global_fee_admin` signs `edit_global_fee_state` with `admin: Some(Pubkey::default())`, all other fields `None`.
2. `edit_fee_state` executes unconditionally, writing `fee_state.global_fee_admin = Pubkey::default()`: [3](#0-2) 
3. Any subsequent call to `edit_global_fee_state` (or any other instruction gated by `has_one = global_fee_admin`) fails the `has_one` constraint for every possible signer, because no keypair exists for the zero address.
4. If the protocol is currently paused (or is paused later), the global fee admin's role in unpausing/administering `pause_delegate_admin` is now permanently unreachable, freezing protocol-wide state changes tied to that role.
5. The identical sequence applies to `configure()` with `new_admin: Some(Pubkey::default())`, bricking a specific group's `admin` role and all `has_one = admin` gated instructions for that group. [7](#0-6)

### Citations

**File:** programs/marginfi/src/state/interest_rate.rs (L51-59)
```rust
    fn validate(&self) -> MarginfiResult {
        match self.curve_type {
            INTEREST_CURVE_LEGACY => self.validate_legacy()?,
            INTEREST_CURVE_SEVEN_POINT => self.validate_seven_point()?,
            _ => panic!("unsupported curve type"),
        }

        Ok(())
    }
```

**File:** programs/marginfi/src/instructions/marginfi_group/configure.rs (L10-27)
```rust
fn validate_and_apply_emode_leverage(
    new_value: Option<WrappedI80F48>,
    current: &mut u32,
) -> MarginfiResult {
    if let Some(wrapped) = new_value {
        let leverage: I80F48 = wrapped.into();
        if leverage < I80F48::ONE {
            msg!("emode leverage {} must be >= 1", leverage);
            return Err(MarginfiError::BadEmodeConfig.into());
        }
        if leverage > I80F48::from_num(100) {
            msg!("emode leverage {} must be <= 100", leverage);
            return Err(MarginfiError::BadEmodeConfig.into());
        }
        *current = basis_to_u32(leverage);
    }
    Ok(())
}
```

**File:** programs/marginfi/src/instructions/marginfi_group/configure.rs (L36-73)
```rust
pub fn configure(
    ctx: Context<MarginfiGroupConfigure>,
    new_admin: Option<Pubkey>,
    new_emode_admin: Option<Pubkey>,
    new_curve_admin: Option<Pubkey>,
    new_limit_admin: Option<Pubkey>,
    new_flow_admin: Option<Pubkey>,
    new_emissions_admin: Option<Pubkey>,
    new_metadata_admin: Option<Pubkey>,
    new_risk_admin: Option<Pubkey>,
    emode_max_init_leverage: Option<WrappedI80F48>,
    emode_max_maint_leverage: Option<WrappedI80F48>,
) -> MarginfiResult {
    let marginfi_group = &mut ctx.accounts.marginfi_group.load_mut()?;
    if let Some(new_admin) = new_admin {
        marginfi_group.update_admin(new_admin);
    }
    if let Some(new_emode_admin) = new_emode_admin {
        marginfi_group.update_emode_admin(new_emode_admin);
    }
    if let Some(new_curve_admin) = new_curve_admin {
        marginfi_group.update_curve_admin(new_curve_admin);
    }
    if let Some(new_limit_admin) = new_limit_admin {
        marginfi_group.update_limit_admin(new_limit_admin);
    }
    if let Some(new_flow_admin) = new_flow_admin {
        marginfi_group.update_flow_admin(new_flow_admin);
    }
    if let Some(new_emissions_admin) = new_emissions_admin {
        marginfi_group.update_emissions_admin(new_emissions_admin);
    }
    if let Some(new_metadata_admin) = new_metadata_admin {
        marginfi_group.update_metadata_admin(new_metadata_admin);
    }
    if let Some(new_risk_admin) = new_risk_admin {
        marginfi_group.update_risk_admin(new_risk_admin);
    }
```

**File:** programs/marginfi/src/instructions/marginfi_group/configure.rs (L115-124)
```rust
#[derive(Accounts)]
pub struct MarginfiGroupConfigure<'info> {
    #[account(
        mut,
        has_one = admin @ MarginfiError::Unauthorized
    )]
    pub marginfi_group: AccountLoader<'info, MarginfiGroup>,

    pub admin: Signer<'info>,
}
```

**File:** programs/marginfi/src/instructions/marginfi_group/edit_global_fee.rs (L24-31)
```rust
    if let Some(admin) = admin {
        msg!(
            "Updating global_fee_admin: {:?} -> {:?}",
            fee_state.global_fee_admin,
            admin
        );
        fee_state.global_fee_admin = admin;
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
