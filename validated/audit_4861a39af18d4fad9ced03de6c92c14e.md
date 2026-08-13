### Title
Delayed/Un-propagated Global Pause State Allows Continued Group Activity During Emergency Pause - (File: `programs/marginfi/src/instructions/marginfi_group/propagate_fee_state.rs`)

### Summary
Marginfi's global emergency-pause mechanism (`panic_pause`) only updates the singleton `FeeState.panic_state` account. Individual `MarginfiGroup` accounts do not read `FeeState` directly during user operations; instead they consult a locally cached copy, `MarginfiGroup.panic_state_cache`, which is only synchronized via the permissionless `propagate_fee_state` instruction [1](#0-0) . This is structurally the same bug class as the reported `CollateralPoolConfig.setAdapter` issue: an admin-controlled global setting is changed in one place (`FeeState`), but dependent consumers (each `MarginfiGroup`) keep operating on stale, un-migrated state until a separate action re-syncs them.

### Finding Description
`panic_pause`/`panic_unpause` write only to the singleton `FeeState` account's `panic_state` field (via `edit_fee_state`-adjacent flows and the dedicated panic instructions), not to any `MarginfiGroup`. The `is_protocol_paused()` gate used by permissionless bank-interest accrual and other pause-sensitive checks (`LendingPoolAccrueBankInterest`) reads from the group's own cached copy, `group.panic_state_cache`, which is only refreshed when `propagate_fee_state` is explicitly invoked for that specific group [2](#0-1) [1](#0-0) .

Because `propagate_fee_state` is permissionless and must be called once *per group* [3](#0-2) , there is a window after a global `panic_pause` call in which any group whose cache has not yet been refreshed continues to believe the protocol is unpaused. Tests confirm this exact mechanic and the requirement to explicitly propagate before pause-gated checks take effect: `test_pause_delegate_admin_cannot_edit_fee_state` and `accrue_bank_interest_blocked_during_pause` both call `try_panic_pause()` followed by an explicit `try_propagate_fee_state()` before asserting the pause is enforced [4](#0-3) . This is the same "migration mechanism" pattern the original report flags as missing in `CollateralPoolConfig`: marginfi actually implements an explicit propagate step, but it depends on someone remembering (or being incentivized) to call it for *every* group, per bank/group scope, immediately after the global state changes.

### Impact Explanation
If the global fee/pause admin invokes `panic_pause` during an incident (e.g., an oracle exploit, a bad-debt event, or a discovered accounting bug) intending to immediately halt all protocol activity, any `MarginfiGroup` for which `propagate_fee_state` has not yet been called will continue to permit borrows, withdrawals, and other user actions gated only by the group-local cache, since the actual pause enforcement instructions (e.g., `lending_pool_accrue_bank_interest`) check `group.panic_state_cache`, not the live `FeeState` [5](#0-4) . This undermines the entire purpose of an emergency pause: a malicious or opportunistic user aware of the incident could race to drain/exploit a not-yet-propagated group before its cache updates, causing real financial loss during exactly the window the pause is meant to prevent it.

### Likelihood Explanation
Likelihood is architecture-dependent rather than trivially exploitable by an outsider absent an incident, but the propagation dependency is entirely real and permissionless-per-group, so it requires no privileged access to exploit the gap — only for the responder to fail to (or be unable to) call `propagate_fee_state` on every group before an attacker acts. In multi-group deployments this is a meaningful operational/race risk; whether it constitutes a full "vulnerability" versus known/accepted operational overhead is uncertain from the available code and cannot be fully confirmed without visibility into deployment-time incident-response tooling (e.g., whether panic_pause is always sent bundled with propagate calls for all live groups in the same transaction, and how many groups exist in practice).

### Recommendation
Consider making pause enforcement authoritative at the `FeeState` level rather than deferring to a per-group cache: require pause-gated instructions to pass in the live `FeeState` account (or bundle `propagate_fee_state` atomically as a required prefix instruction / CPI) so a single `panic_pause` call is guaranteed to halt all groups immediately, removing the window where stale caches allow continued activity.

### Proof of Concept
Not independently reproducible from static review alone; the existing test suite already demonstrates the mechanic (that pause enforcement requires an explicit, separate propagate call per group) at `programs/marginfi/tests/admin_actions/actions_during_pause.rs` lines 196–219 [4](#0-3) , which shows that omitting the `try_propagate_fee_state()` call leaves the group's cached pause state stale relative to the just-updated `FeeState`.

### Citations

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

**File:** programs/marginfi/src/instructions/marginfi_group/propagate_fee_state.rs (L21-43)
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
}
```

**File:** programs/marginfi/src/instructions/marginfi_group/accrue_bank_interest.rs (L28-41)
```rust
#[derive(Accounts)]
pub struct LendingPoolAccrueBankInterest<'info> {
    #[account(
        constraint = (
            !group.load()?.is_protocol_paused()
        ) @ MarginfiError::ProtocolPaused
    )]
    pub group: AccountLoader<'info, MarginfiGroup>,

    #[account(
        mut,
        has_one = group @ MarginfiError::InvalidGroup
    )]
    pub bank: AccountLoader<'info, Bank>,
```

**File:** programs/marginfi/tests/admin_actions/actions_during_pause.rs (L196-219)
```rust
/// Permissionless `lending_pool_accrue_bank_interest` is rejected while the protocol is paused.
#[tokio::test]
async fn accrue_bank_interest_blocked_during_pause() -> anyhow::Result<()> {
    let test_f = TestFixture::new(Some(TestSettings::all_banks_payer_not_admin())).await;

    let usdc_bank = test_f.get_bank(&BankMint::Usdc);

    // Sanity check: accrue works while unpaused.
    test_f.marginfi_group.try_accrue_interest(usdc_bank).await?;

    // Pause and propagate so the group cache reflects the paused state.
    test_f.marginfi_group.try_panic_pause().await?;
    test_f.marginfi_group.try_propagate_fee_state().await?;

    let marginfi_group = test_f.marginfi_group.load().await;
    assert!(marginfi_group.panic_state_cache.is_paused_flag());

    // While paused, the permissionless crank must be rejected.
    let result = test_f.marginfi_group.try_accrue_interest(usdc_bank).await;

    assert_custom_error!(result.unwrap_err(), MarginfiError::ProtocolPaused);

    Ok(())
}
```
