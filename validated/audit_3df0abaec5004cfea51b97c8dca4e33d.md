Based on my investigation, I found a strong analog to the reported bug class.

### Title
Orders can be closed while the protocol is paused, bypassing the pause's intended freeze on order flows - ([File: programs/marginfi/src/instructions/marginfi_account/order.rs])

### Summary
The `PlaceOrder` accounts struct in marginfi enforces a pause check (`!group.load()?.is_protocol_paused()` @ `MarginfiError::ProtocolPaused`), consistent with the documented rule that "Order placement / order flows" are blocked while the protocol is paused. However, the `CloseOrder` struct — used by `marginfi_account_close_order` — does not include this pause constraint on its `group` account, meaning any account authority can remove/close an active protective order (stop-loss/take-profit) even while the protocol is in an emergency-paused state.

### Finding Description
`CloseOrder` is defined at [1](#0-0)  and only checks that the account is not frozen and that the caller is an authorized signer — it has no `is_protocol_paused()` constraint on `group`. By contrast, sibling instructions like `TransferToNewAccount`/`TransferToNewAccountPda` explicitly gate on `!group.load()?.is_protocol_paused()` @ `MarginfiError::ProtocolPaused` [2](#0-1)  and [3](#0-2) . The `close_order` handler itself performs no pause check either [4](#0-3) .

The project's own documentation states that "Order placement / order flows" are supposed to be blocked while paused, as part of the narrow, deliberate exception list (forced deleverage, admin-triggered bankruptcy handling, and unpause) being the *only* permitted actions during a pause [5](#0-4) . `CloseOrder` is not in that exception list, yet it remains callable during a pause because the constraint was omitted from its `Accounts` struct — the same root-cause pattern as the reported `removeRedirectedOffChainDistribution` bug (a state-removal function missing the pause gate that its sibling/counterpart functions enforce).

### Impact Explanation
An order (stop-loss/take-profit) is a user-configured risk-management primitive tied to specific account balances. Pausing the protocol is meant to freeze the system in a critical/incident scenario so admins can resolve things without users taking actions that alter risk exposure. If a user (or an attacker who has compromised a user's or delegate's signing key) can freely close protective orders during a pause, they can remove risk-management guardrails exactly during the window when the protocol is most vulnerable (e.g., an oracle/exploit incident causing the pause), undermining the very reason for pausing. This is a genuine, reachable authorization/state-consistency gap: a permissioned "remove" action executes when it should be blocked, matching the report's core issue (removal path missing the pause gate that governs the corresponding user flow).

### Likelihood Explanation
Likelihood is moderate: `CloseOrder` requires the account authority (or an admin acting on their behalf) to sign, so it is not fully permissionless, but it is trivially reachable by any user with an active order at any time the protocol happens to be paused — no special conditions are needed beyond an active pause and an existing order. Given panic-pause is an intended, periodically-exercised safety mechanism (it has rate limits: 4 consecutive pauses, 3/day per the docs), the paused window is a realistic and recurring state during which this gap is exploitable.

### Recommendation
Add the same pause constraint used elsewhere (e.g., in `TransferToNewAccount`) to `CloseOrder`'s `group` account:
```rust
#[account(
    constraint = (!group.load()?.is_protocol_paused()) @ MarginfiError::ProtocolPaused
)]
pub group: AccountLoader<'info, MarginfiGroup>,
```
Also verify whether `KeeperCloseOrder` and `SetKeeperCloseFlags` should be similarly gated, consistent with the documented "Order placement / order flows" pause behavior.

### Proof of Concept
Not independently executed; based on static code review:
1. Admin/`global_fee_admin` calls `panic_pause`, setting `panic_state_cache.is_paused_flag()` true (verified pattern in test setup, e.g. [6](#0-5)  shows borrow correctly fails with `ProtocolPaused` during pause).
2. A user with an existing `Order` PDA calls `marginfi_account_close_order` (`CloseOrder`) while the group's pause flag is active.
3. Because `CloseOrder`'s `group` account has no `is_protocol_paused()` constraint (unlike `TransferToNewAccount`), the instruction succeeds, decrementing `active_orders` and closing the `Order` account for rent — contrary to the documented "Order placement / order flows" being blocked during pause.

Note: I did not find an existing regression test asserting `CloseOrder` fails during pause (the pause test suite covers deposit/withdraw/borrow/repay/liquidation/bankruptcy/interest-accrual, but not `close_order`), which is consistent with this being an overlooked gap rather than an intentionally-permitted exception.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_account/order.rs (L139-161)
```rust
pub fn close_order(ctx: Context<CloseOrder>) -> MarginfiResult {
    let CloseOrder {
        marginfi_account: marginfi_account_loader,
        authority,
        order: order_loader,
        ..
    } = &ctx.accounts;

    let mut marginfi_account = marginfi_account_loader.load_mut()?;
    marginfi_account.decrement_active_orders()?;

    emit!(MarginfiAccountCloseOrderEvent {
        header: AccountEventHeader {
            signer: Some(authority.key()),
            marginfi_account: marginfi_account_loader.key(),
            marginfi_account_authority: marginfi_account.authority,
            marginfi_group: marginfi_account.group,
        },
        order: order_loader.key(),
    });

    Ok(())
}
```

**File:** programs/marginfi/src/instructions/marginfi_account/order.rs (L582-615)
```rust
#[derive(Accounts)]
pub struct CloseOrder<'info> {
    pub group: AccountLoader<'info, MarginfiGroup>,

    #[account(
        mut,
        has_one = group @ MarginfiError::InvalidGroup,
        constraint = {
            let a = marginfi_account.load()?;
            account_not_frozen_for_authority(&a, authority.key())
        } @ MarginfiError::AccountFrozen,
        constraint = {
            let a = marginfi_account.load()?;
            let g = group.load()?;
            is_signer_authorized(&a, g.admin, authority.key(), false, false)
        } @ MarginfiError::Unauthorized
    )]
    pub marginfi_account: AccountLoader<'info, MarginfiAccount>,

    pub authority: Signer<'info>,

    #[account(
        mut,
        has_one = marginfi_account,
        close = fee_recipient
    )]
    pub order: AccountLoader<'info, Order>,

    /// CHECK: no checks whatsoever, marginfi account authority decides this without restriction
    #[account(mut)]
    pub fee_recipient: UncheckedAccount<'info>,

    pub system_program: Program<'info, System>,
}
```

**File:** programs/marginfi/src/instructions/marginfi_account/transfer_account.rs (L122-129)
```rust
#[derive(Accounts)]
pub struct TransferToNewAccount<'info> {
    #[account(
        constraint = (
            !group.load()?.is_protocol_paused()
        ) @ MarginfiError::ProtocolPaused
    )]
    pub group: AccountLoader<'info, MarginfiGroup>,
```

**File:** programs/marginfi/src/instructions/marginfi_account/transfer_account.rs (L267-275)
```rust
#[derive(Accounts)]
#[instruction(account_index: u16, third_party_id: Option<u16>)]
pub struct TransferToNewAccountPda<'info> {
    #[account(
        constraint = (
            !group.load()?.is_protocol_paused()
        ) @ MarginfiError::ProtocolPaused
    )]
    pub group: AccountLoader<'info, MarginfiGroup>,
```

**File:** guides/ADMIN/PERMISSIONS_AND_ROLES.md (L147-176)
```markdown
### Blocked while paused

All normal user flows are disabled:

- Deposit, Borrow, Withdraw, Repay (both native banks and integration banks — Kamino, Drift,
  Juplend, Solend)
- Order placement / order flows
- Account transfer
- Classic liquidation (`LendingAccountLiquidate`)
- Permissionless bank-fee collection
- Permissionless bad-debt settlement (`HandleBankruptcy` when called by a non-admin, even on banks
  with the `PERMISSIONLESS_BAD_DEBT_SETTLEMENT` flag)
- Admin bank configuration changes that route through `LendingPoolConfigureBank`

### Permitted while paused (admin exceptions)

A narrow set of actions remain available so the admin/risk_admin can actually resolve the
incident the pause was called for:

- **Forced deleverage** — `risk_admin` can run the full deleverage flow (`StartLiquidation` in
  deleverage mode, plus the withdraw/repay instructions that execute while
  `ACCOUNT_IN_DELEVERAGE` is set). The pause checks on withdraw/repay (including integration
  withdrawals on Kamino, Drift, Juplend, Solend) are bypassed when the account carries this flag,
  so a deleverage can be completed end-to-end.
- **Handle bankruptcy by admin** — `admin` or `risk_admin` can call `HandleBankruptcy` while
  paused. This is needed because a forced deleverage often terminates in a bankruptcy, and
  blocking bankruptcy would leave the bank in a half-resolved state. Non-admin callers (even on
  banks with `PERMISSIONLESS_BAD_DEBT_SETTLEMENT`) remain blocked until the pause expires.
- **Unpause** — `global_fee_admin` can always end the pause early via `panic_unpause`, and anyone
  can permissionlessly clear an expired pause via `panic_unpause_permissionless`.
```

**File:** programs/marginfi/tests/user_actions/panic_mode_user_interactions.rs (L200-213)
```rust
    let borrow_token_account = test_f.sol_mint.create_empty_token_account().await;

    test_f.marginfi_group.try_panic_pause().await?;

    test_f.marginfi_group.try_propagate_fee_state().await?;

    let marginfi_group = test_f.marginfi_group.load().await;
    assert!(marginfi_group.panic_state_cache.is_paused_flag());

    let result = borrower_account_f
        .try_bank_borrow(borrow_token_account.key, sol_bank_f, 1)
        .await;

    assert_custom_error!(result.unwrap_err(), MarginfiError::ProtocolPaused);
```
