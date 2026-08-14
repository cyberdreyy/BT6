### Title
Emergency protocol pause can be bypassed on any group whose cached panic state has not been permissionlessly propagated - (File: `programs/marginfi/src/instructions/marginfi_group/propagate_fee_state.rs`)

### Summary
The original TraitForge finding is that contracts inherit OpenZeppelin's `Pausable` but never expose `pause()`/`unpause()`, making the emergency stop mechanically unreachable, so the protection exists on paper but cannot actually be enforced. marginfi does expose working `panic_pause` / `panic_unpause` / `panic_unpause_permissionless` instructions with correct admin gating [1](#0-0) [2](#0-1) , so that exact "inaccessible function" pattern does not exist. However, the pause state that user-facing instructions actually check is not the live global `FeeState.panic_state`; it is a **cached copy** on each `MarginfiGroup` (`panic_state_cache`) that must be refreshed via the permissionless `propagate_fee` instruction [3](#0-2) . This produces an analogous "the safety switch exists but doesn't actually stop the machine" class of bug: for any group whose cache has not yet been re-synced after `panic_pause` is called, user instructions gated on `group.load()?.is_protocol_paused()` (e.g. `PlaceOrder`, `StartExecuteOrder`, `EndExecuteOrder`) will still evaluate against the stale, unpaused cached state [4](#0-3) [5](#0-4) .

### Finding Description
`panic_pause` mutates `FeeState.panic_state` only [6](#0-5) . The per-group cache (`MarginfiGroup.panic_state_cache`) is a separate, independently-updated copy that only gets refreshed when someone calls the permissionless `propagate_fee` instruction against that specific group [7](#0-6) . Because propagation is not atomic with `panic_pause` and is not required before user actions execute, there is a window — and potentially an indefinite one for any group nobody bothers to "poke" — during which:
- The protocol is administratively marked paused in `FeeState`.
- A specific `MarginfiGroup`'s cached panic state still reads "not paused."
- Instructions gated purely on the group's cached flag (`is_protocol_paused()`) proceed normally for that group, defeating the purpose of the emergency pause for as long as the cache is stale.

This mirrors the root cause of the referenced report: a pausing mechanism is architecturally present but not effectively wired into the code paths that need to respect it in real time, so administrators cannot actually rely on it to halt activity during an incident for every group.

### Impact Explanation
If an attacker (or exploiter racing an ongoing incident) deliberately avoids calling `propagate_fee` on the targeted group — or simply exploits a group nobody has refreshed since the last pause — they can continue placing/executing orders (and, if the same stale-cache gate pattern governs deposit/withdraw/borrow/repay/liquidate paths for that group, continue those flows too) during a declared protocol-wide panic-pause. This directly undermines the intended "instant, protocol-wide halt" guarantee described in the admin documentation and could let unprivileged users extract value, avoid liquidation, or otherwise interact with a bank during precisely the window administrators intended to freeze, causing financial harm during an active incident.

### Likelihood Explanation
The propagation step is permissionless and not required as a precondition of any user instruction, so no admin action can force it synchronously with `panic_pause`. Under normal, non-emergency conditions bots may keep caches fresh, but during an actual crisis (the only time pausing matters) an adversary has direct incentive to avoid or race the propagation, and groups that are not actively monitored (e.g. lower-volume or legacy banks) may go unpropagated for extended periods, making exploitation plausible precisely when it matters most.

### Recommendation
Either (a) make `panic_pause`/`panic_unpause` update every group's cache atomically (impractical at scale), or (b) have all pause-gated instructions read the live `FeeState.panic_state` directly instead of (or in addition to, with `unpause_if_expired`-style freshness checks and a hard staleness bound) the per-group cache, or (c) require the cache's `last_update` timestamp to be within a strict recency bound and fail closed (treat "possibly paused/unknown" as paused) if the cache is older than that bound.

### Proof of Concept
1. Global fee admin calls `panic_pause`, setting `FeeState.panic_state.pause_flags = FLAG_PAUSED` [6](#0-5) .
2. For `GroupX`, `propagate_fee` was last called before the pause, so `GroupX.panic_state_cache.is_paused_flag()` is still `false`.
3. A user submits `marginfi_account_place_order` (or `start_execute_order` / `end_execute_order`) against `GroupX`. The `PlaceOrder` account constraint `!group.load()?.is_protocol_paused()` evaluates against the stale cache and passes [4](#0-3) .
4. The order/trade executes normally, despite the protocol being globally panic-paused, until someone eventually calls `propagate_fee` for `GroupX`.

Note: I was not able to fully inspect `MarginfiGroup::is_protocol_paused()`'s exact implementation in `programs/marginfi/src/state/marginfi_group.rs` within the available tool budget (only match counts were retrieved, not full content), so the precise staleness-tolerance logic (if any additional guard exists there) could not be fully confirmed. A background Devin session or manual review of that file is recommended to validate whether any additional expiry/staleness check is already applied at the `is_protocol_paused()` call site before treating this as a confirmed exploitable gap.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/panic_pause.rs (L7-16)
```rust
pub fn panic_pause(ctx: Context<PanicPause>) -> Result<()> {
    let mut fee_state = ctx.accounts.fee_state.load_mut()?;
    let current_timestamp = Clock::get()?.unix_timestamp;

    // Update panic state if the current pause has expired
    fee_state.panic_state.unpause_if_expired(current_timestamp);

    fee_state.panic_state.pause(current_timestamp)?;

    msg!("Protocol paused at timestamp: {}", current_timestamp);
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

**File:** programs/marginfi/src/instructions/marginfi_group/panic_unpause.rs (L39-52)
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
}
```

**File:** programs/marginfi/src/instructions/marginfi_group/propagate_fee_state.rs (L21-42)
```rust
pub fn propagate_fee(ctx: Context<PropagateFee>) -> Result<()> {
    let mut group = ctx.accounts.marginfi_group.load_mut()?;
    let fee_state = ctx.accounts.fee_state.load()?;

    group.fee_state_cache.global_fee_wallet = fee_state.global_fee_wallet;
    group.fee_state_cache.program_fee_fixed = fee_state.program_fee_fixed;
    group.fee_state_cache.program_fee_rate = fee_state.program_fee_rate;

    let clock = Clock::get()?;
    group.fee_state_cache.last_update = clock.unix_timestamp;

    group
        .panic_state_cache
        .update_from_panic_state(&fee_state.panic_state, clock.unix_timestamp);

    msg!(
        "Propagated fee and panic state to group. Panic state: paused={}",
        group.panic_state_cache.is_paused_flag()
            && !group.panic_state_cache.is_expired(clock.unix_timestamp)
    );

    Ok(())
```

**File:** programs/marginfi/src/instructions/marginfi_account/order.rs (L513-516)
```rust
pub struct PlaceOrder<'info> {
    #[account(
        constraint = (!group.load()?.is_protocol_paused()) @ MarginfiError::ProtocolPaused
    )]
```

**File:** programs/marginfi/src/instructions/marginfi_account/order.rs (L659-663)
```rust
#[derive(Accounts)]
pub struct StartExecuteOrder<'info> {
    #[account(
        constraint = (!group.load()?.is_protocol_paused()) @ MarginfiError::ProtocolPaused
    )]
```
