Given the evidence gathered, there is a genuine analog to the reported bug class in `marginfi-v2`. The `global_fee_wallet` is analogous to the reported `withdrawalWallet`: it is stored authoritatively on the singleton `FeeState` account and can be rotated at any time by the `global_fee_admin`, but several consumer instructions do not read that authoritative value — they read a per-`MarginfiGroup` cached copy (`fee_state_cache.global_fee_wallet`) that is only refreshed by a separate, decoupled, permissionless instruction (`propagate_fee`).

### Title
Stale cached `global_fee_wallet` lets fee/rent flows continue to a wallet the admin has deliberately rotated away from - ([File: programs/marginfi/src/instructions/marginfi_group/propagate_fee_state.rs])

### Summary
`FeeState.global_fee_wallet` is the single source of truth for the protocol's fee-receiving wallet and can be updated at any time via `edit_global_fee_state` [1](#0-0) . However, each `MarginfiGroup` keeps its own cached copy, `fee_state_cache.global_fee_wallet`, which is set at group-init time [2](#0-1)  and only refreshed by the separate, permissionless `propagate_fee` instruction [3](#0-2) . Several fee/rent-routing instructions validate against this cached, potentially stale value instead of the live `FeeState`, so after an admin rotates `global_fee_wallet`, funds can keep flowing to the old wallet on any group that has not yet had `propagate_fee` re-run against it.

### Finding Description
The `admin_close_account` instruction closes an inactive account and sends its rent to whatever `global_fee_wallet` is passed in, only constraining it to equal the group's *cached* value: [4](#0-3) 

Similarly, `transfer_to_new_account`/`transfer_to_new_account_pda` collect a flat SOL fee (`ACCOUNT_TRANSFER_FEE`) and route it to whatever wallet matches `group.fee_state_cache.global_fee_wallet`, again the cached, not live, value: [5](#0-4) [6](#0-5) 

By contrast, other integration paths (e.g. `drift_claim_bad_debt`, `drift_harvest_reward`) correctly read the live `FeeState.global_fee_wallet` directly rather than the cache [7](#0-6) [8](#0-7) , confirming the cached-value paths are an inconsistency in the codebase rather than an intentional universal design choice.

The cache is only synchronized by explicitly invoking `propagate_fee`, which must be called once per `MarginfiGroup` (there is no bound on the number of groups, and group creation itself is permissionless-admin-driven) [9](#0-8) . There is no atomicity or enforcement tying a `global_fee_wallet` rotation to propagation across all existing groups — this mirrors the reported bug class where a "withdrawal wallet" is upgradeable in one place (here, `FeeState`, the authoritative store) but other consuming logic (here, per-group cached admin-close/transfer-fee flows) keeps referencing the old address until manually resynced.

### Impact Explanation
If the `global_fee_admin` rotates `global_fee_wallet` — for example because the current wallet's key is suspected compromised, or ownership of the wallet is being transferred — every `MarginfiGroup` whose cache has not yet been refreshed via `propagate_fee` will continue to:
- Send rent from `admin_close_account` (closing any eligible inactive account) to the OLD wallet.
- Send the `ACCOUNT_TRANSFER_FEE` from `transfer_to_new_account`/`transfer_to_new_account_pda` to the OLD wallet.

This results in a durable value-redirection window: legitimate protocol fee/rent revenue is sent to a wallet the operator explicitly intended to deprecate (potentially attacker-controlled if rotation was compromise-driven), for every group lagging in propagation, and there is no way to force a specific group's cache to update — it can only be nudged in permissionless fashion, and nothing about the design flags or blocks operations against groups still on the stale wallet.

### Likelihood Explanation
This requires (1) an admin-initiated `global_fee_wallet` rotation and (2) at least one `MarginfiGroup` for which `propagate_fee` has not yet been re-invoked. Since group creation and fee-wallet rotation are both plausible, expected operational events, and `propagate_fee` execution is not automatically bundled with `edit_global_fee_state`, this window is realistic, especially as the number of groups grows (permissionless bank/group ecosystem) making full propagation slower/less certain across all of them.

### Recommendation
Either (a) have `admin_close_account` and `transfer_to_new_account`/`transfer_to_new_account_pda` validate against the live `FeeState.global_fee_wallet` (as `drift_claim_bad_debt`/`drift_harvest_reward` already do) instead of the per-group cache, or (b) make `edit_global_fee_state` require/trigger propagation to all groups atomically, or at minimum emit a clear signal/version bump that downstream consumers must check before trusting the cached wallet.

### Proof of Concept
1. `global_fee_admin` calls `edit_global_fee_state` with a new `fee_wallet` to rotate away from `wallet_old` (e.g., due to a suspected key compromise) [1](#0-0) .
2. For any existing `MarginfiGroup` `G` where `propagate_fee` has not yet been called since the rotation, `G.fee_state_cache.global_fee_wallet` still equals `wallet_old` (last synced at group creation or previous propagation) [3](#0-2) .
3. Anyone (permissionless) calls `admin_close_account` for an eligible inactive account under group `G`, passing `wallet_old`'s account as `global_fee_wallet`; the constraint `global_fee_wallet.key() == group.load()?.fee_state_cache.global_fee_wallet` passes, and the account's rent is closed out to `wallet_old` [10](#0-9) .
4. Likewise, any account authority under group `G` calling `transfer_to_new_account`/`transfer_to_new_account_pda` will have their `ACCOUNT_TRANSFER_FEE` routed to `wallet_old` rather than the newly-rotated wallet [5](#0-4) .

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/edit_global_fee.rs (L32-39)
```rust
    if let Some(fee_wallet) = fee_wallet {
        msg!(
            "Updating global_fee_wallet: {:?} -> {:?}",
            fee_state.global_fee_wallet,
            fee_wallet
        );
        fee_state.global_fee_wallet = fee_wallet;
    }
```

**File:** programs/marginfi/src/instructions/marginfi_group/initialize.rs (L30-30)
```rust
    marginfi_group.fee_state_cache.global_fee_wallet = fee_state.global_fee_wallet;
```

**File:** programs/marginfi/src/instructions/marginfi_group/propagate_fee_state.rs (L7-19)
```rust
#[derive(Accounts)]
pub struct PropagateFee<'info> {
    // Note: there is just one FeeState per program, so no further check is required.
    #[account(
        seeds = [FEE_STATE_SEED.as_bytes()],
        bump,
    )]
    pub fee_state: AccountLoader<'info, FeeState>,

    /// Any group, this ix is permisionless and can propagate the fee to any group
    #[account(mut)]
    pub marginfi_group: AccountLoader<'info, MarginfiGroup>,
}
```

**File:** programs/marginfi/src/instructions/marginfi_group/propagate_fee_state.rs (L21-27)
```rust
pub fn propagate_fee(ctx: Context<PropagateFee>) -> Result<()> {
    let mut group = ctx.accounts.marginfi_group.load_mut()?;
    let fee_state = ctx.accounts.fee_state.load()?;

    group.fee_state_cache.global_fee_wallet = fee_state.global_fee_wallet;
    group.fee_state_cache.program_fee_fixed = fee_state.program_fee_fixed;
    group.fee_state_cache.program_fee_rate = fee_state.program_fee_rate;
```

**File:** programs/marginfi/src/instructions/marginfi_account/admin_close.rs (L58-71)
```rust
    #[account(
        mut,
        has_one = group @ MarginfiError::InvalidGroup,
        close = global_fee_wallet
    )]
    pub marginfi_account: AccountLoader<'info, MarginfiAccount>,

    /// CHECK: Validated against group fee state cache
    #[account(
        mut,
        constraint = global_fee_wallet.key() == group.load()?.fee_state_cache.global_fee_wallet
            @ MarginfiError::InvalidGlobalFeeWallet
    )]
    pub global_fee_wallet: UncheckedAccount<'info>,
```

**File:** programs/marginfi/src/instructions/marginfi_account/transfer_account.rs (L51-59)
```rust
pub fn transfer_to_new_account(ctx: Context<TransferToNewAccount>) -> MarginfiResult {
    // Validate the global fee wallet and claim a nominal fee
    let group = ctx.accounts.group.load()?;
    check_eq!(
        ctx.accounts.global_fee_wallet.key(),
        group.fee_state_cache.global_fee_wallet,
        MarginfiError::InvalidFeeAta
    );
    anchor_lang::system_program::transfer(ctx.accounts.transfer_fee(), ACCOUNT_TRANSFER_FEE)?;
```

**File:** programs/marginfi/src/instructions/marginfi_account/transfer_account.rs (L186-194)
```rust
) -> MarginfiResult {
    // Validate the global fee wallet and claim a nominal fee
    let group = ctx.accounts.group.load()?;
    check_eq!(
        ctx.accounts.global_fee_wallet.key(),
        group.fee_state_cache.global_fee_wallet,
        MarginfiError::InvalidFeeAta
    );
    anchor_lang::system_program::transfer(ctx.accounts.transfer_fee(), ACCOUNT_TRANSFER_FEE)?;
```

**File:** programs/marginfi/src/instructions/drift/claim_bad_debt.rs (L129-131)
```rust
    /// CHECK: Must match FeeState.global_fee_wallet. Used as the owner for the destination ATA.
    #[account(address = fee_state.load()?.global_fee_wallet @ MarginfiError::InvalidFeeAta)]
    pub global_fee_wallet: UncheckedAccount<'info>,
```

**File:** programs/marginfi/src/instructions/drift/harvest_reward.rs (L83-90)
```rust
    /// Destination token account must be owned by the global fee wallet
    #[account(
        mut,
        associated_token::mint = reward_mint,
        associated_token::authority = fee_state.load()?.global_fee_wallet,
        associated_token::token_program = token_program,
    )]
    pub destination_token_account: Box<InterfaceAccount<'info, TokenAccount>>,
```
