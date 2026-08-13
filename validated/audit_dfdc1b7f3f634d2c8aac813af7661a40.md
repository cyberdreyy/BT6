Based on my research, I found the strongest applicable analog: the **Order** system's `PlaceOrder`/`StartExecuteOrder`/`EndExecuteOrder` flow.

### Title
Orders placed on a bank that later becomes ineligible (Paused/killed/frozen) can become permanently unexecutable, trapping user positions and Keeper economics indefinitely - (File: `programs/marginfi/src/instructions/marginfi_account/order.rs`)

### Summary
Like the Derby `game.sol` bug, where a `deltaAllocation` could be committed to persistent state via a permissionless action referencing a protocol that later gets blacklisted, breaking the downstream `rebalance()` step irrecoverably, marginfi's `Order` system allows a user to permissionlessly `PlaceOrder` (commit state referencing two banks) at one point in time, while the later, required execution step (`StartExecuteOrder`/`EndExecuteOrder`, run by a permissionless Keeper) can be blocked forever if one of the referenced banks transitions to a state that the health/pricing/oracle checks reject (e.g., `Paused`, `KilledByBankruptcy`, or a stale/disabled oracle) [1](#0-0) .

### Finding Description
`PlaceOrder` commits a persistent `Order` account referencing a `bank_1`/`bank_2` pair and a trigger threshold [2](#0-1) . Execution is a separate, later transaction pair run by a permissionless Keeper: `StartExecuteOrder` computes `get_tagged_account_health_components` over the order's tagged balances and requires a group-not-paused constraint [3](#0-2) [4](#0-3) , then `EndExecuteOrder` similarly requires the group not be paused [5](#0-4) . If the group enters `panic_pause`, or if one of the order's referenced banks is later set to `Paused`/`KilledByBankruptcy` by the admin (an action fully independent from the order's placement), the execution flow will revert on every attempt — exactly analogous to Derby's `setDeltaAllocationsInt` reverting on a blacklisted protocol and blocking `rebalance()`. The documentation itself acknowledges a related but narrower version of this durability problem: "if the user closes the SOL Balance and deposits SOL later, then this Order CANNOT EXECUTE, it is orphaned" and orders can be "orphaned" and simply "STAY ON THE BOOKS" indefinitely with no permissionless cleanup other than closing by the user themselves or `KeeperCloseOrder`, which only works if the tags no longer exist on the account [6](#0-5) . Unlike Derby's `deltas`, orders don't hold escrowed funds directly, but a Keeper cannot execute a stop-loss/take-profit that was relying on a bank that has since been administratively paused/killed, meaning the user's intended risk-management trigger silently fails to protect them during exactly the kind of volatile/emergency period (protocol pause, bank pause, bankruptcy) when it is needed most.

### Impact Explanation
Unlike Derby's `deltas`, no protocol funds are frozen by this specific path, and the account itself is not blocked from other user-initiated actions (deposit/withdraw/repay can still be attempted independently, subject to the bank's own operational state) — so this does not rise to the "whole system down" severity of the Derby finding. The practical effect is: a user's stop-loss/take-profit order becomes permanently unexecutable during exactly the volatility/emergency window (protocol panic-pause or bank pause/bankruptcy) it was designed to protect against, and a Keeper cannot collect the expected execution fee/profit. This is a real but narrower fidelity gap versus the Derby M-23 pattern.

### Likelihood Explanation
Requires either (a) the `global_fee_admin`/`pause_delegate_admin` invoking `panic_pause` (privileged) or (b) the group admin pausing/killing a specific bank the order references (privileged action, not attacker-controlled). The order is placed by an ordinary unprivileged user, and any Keeper (unprivileged) is expected to execute it — so the "victim" side of the bug is unprivileged, but the trigger (bank/protocol pause) is admin-driven, not attacker-forced. This makes it a lower-likelihood, admin-emergency-adjacent scenario rather than a directly attacker-exploitable one, unlike Derby's fully user-triggerable blacklist-allocation bug.

### Recommendation
Document (or better, technically enforce) that Orders referencing a bank that transitions to `Paused`/`KilledByBankruptcy`/protocol-panic-pause should either: (1) allow `CloseOrder`/`KeeperCloseOrder` to succeed even while the group/bank is paused so users/keepers can always tear down an order that can no longer execute, or (2) allow `StartExecuteOrder`/`EndExecuteOrder` to bypass the protocol-pause constraint in a restricted, health-improving-only mode (similar to the deleverage pause-bypass already implemented) so pending stop-loss/take-profit protection is not silently disabled during the exact emergency window it exists for.

### Proof of Conception
1. User calls `PlaceOrder` with `bank_1`=SOL, `bank_2`=USDC, a Stop-Loss trigger.
2. Admin later invokes `panic_pause` (or pauses the SOL bank) in response to an incident.
3. SOL price crashes past the stop-loss threshold.
4. A Keeper attempts `StartExecuteOrder`/`EndExecuteOrder`; both instructions carry a `!group.load()?.is_protocol_paused()` constraint [7](#0-6) [8](#0-7)  and revert with `ProtocolPaused`.
5. The user's stop-loss never executes for the duration of the pause (and, if the referenced bank remains `Paused`/`KilledByBankruptcy` afterward, indefinitely), leaving the position unprotected exactly when protection was needed.

### Citations

**File:** guides/USER/ORDERS.md (L1-16)
```markdown
# Summary

An `Order` is a stop-loss and/or take-profit trigger that a `Keeper` can permissionlessly execute.
When creating an Order, users choose an asset pair (a lending asset and a borrowing asset), a
trigger point to execute the order, and the type of order (Stop Loss, Take Profit, or Both).

- A `Stop Loss` executes when the pair of assets falls below a certain value.
- A `Take Profit` executes when the pair of assets goes above a certain value.
- `Both` allows the user to set a Stop Loss and Take profit threshold in the same Order. (F1)

### Order Execution

The borrow-side position of an Order is always closed in full. The lending position is never closed
(F2). This means if you have a \$200 SOL lend and \$100 USDC borrow, and you would like to close
just half of your net LONG position with an order, you will have to create two accounts with \$100
SOL and \$50 USDC each!
```

**File:** guides/USER/ORDERS.md (L80-84)
```markdown
(F2) The lending position can be withdrawn down to $0, but must remain open. If the Balance is closed
by the user (e.g. by withdraw_all), and the same asset is deposited later to re-open it, Orders
created prior to the Balance being closed **will not work**. This means users are able to modify
their accounts such that active Orders are orphaned and can no longer execute, it's up to users to make
sure they do not close out positions involved with their Orders without updating the Orders too.
```

**File:** guides/USER/ORDERS.md (L99-111)
```markdown
## Instructions

- `PlaceOrder` (user) - Place a new Stop Loss, Take Profit, or Both type Order on a pair of balances
  the user currently holds.
- `StartExecuteOrder` (Keeper) - Keepers run this to begin the execution of an Order. Must be at the
  start of the tx. Withdraw/Repay of the involved balances typically follows this ix.
  Requires a risk check of just the balances involved in the Order.
- `EndExecuteOrder` (Keeper) - Must be the last tx in executing an Order. Requires a risk check of
  just the balances involved in the Order.
- `CloseOrder` (user) - Clear an unwanted Order, user gets their rent back.
- `SetKeeperCloseFlags` (user) - Enables the Keeper to close Orders via `KeeperCloserOrder`,
  typically use `CloseOrder` instead.
- `KeeperCloserOrder` (Keeper) - Close an Order on an account where neither of the original positions exists or all the tags have been cleared by the user
```

**File:** programs/marginfi/src/instructions/marginfi_account/order.rs (L271-298)
```rust
pub fn start_execute_order<'info>(ctx: Context<'info, StartExecuteOrder<'info>>) -> MarginfiResult {
    let StartExecuteOrder {
        marginfi_account: marginfi_account_loader,
        fee_payer: _fee_payer,
        executor,
        order: order_loader,
        execute_record: execute_record_loader,
        instruction_sysvar,
        ..
    } = &ctx.accounts;

    let mut marginfi_account = marginfi_account_loader.load_mut()?;
    let mut order = order_loader.load_mut()?;

    marginfi_account.set_flag(ACCOUNT_IN_ORDER_EXECUTION, false);

    let (order_assets_in_equity, order_liabs_in_equity, order_asset_count, order_liab_count) =
        get_tagged_account_health_components(
            &marginfi_account,
            ctx.remaining_accounts,
            &order.tags,
        )?;

    check!(
        order_asset_count + order_liab_count == ORDER_ACTIVE_TAGS,
        MarginfiError::LendingAccountBalanceNotFound
    );

```

**File:** programs/marginfi/src/instructions/marginfi_account/order.rs (L659-680)
```rust
#[derive(Accounts)]
pub struct StartExecuteOrder<'info> {
    #[account(
        constraint = (!group.load()?.is_protocol_paused()) @ MarginfiError::ProtocolPaused
    )]
    pub group: AccountLoader<'info, MarginfiGroup>,

    /// The account owning the order
    #[account(
        mut,
        has_one = group @ MarginfiError::InvalidGroup,
        constraint = {
            let acc = marginfi_account.load()?;
            !acc.get_flag(ACCOUNT_IN_ORDER_EXECUTION)
                && !acc.get_flag(ACCOUNT_IN_FLASHLOAN)
                && !acc.get_flag(ACCOUNT_FROZEN)
                && !acc.get_flag(ACCOUNT_DISABLED)
                && !acc.get_flag(ACCOUNT_IN_RECEIVERSHIP)
                && !acc.get_flag(ACCOUNT_IN_DELEVERAGE)
        } @MarginfiError::UnexpectedOrderExecutionState
    )]
    pub marginfi_account: AccountLoader<'info, MarginfiAccount>,
```

**File:** programs/marginfi/src/instructions/marginfi_account/order.rs (L725-746)
```rust
#[derive(Accounts)]
pub struct EndExecuteOrder<'info> {
    #[account(
        constraint = (!group.load()?.is_protocol_paused()) @ MarginfiError::ProtocolPaused
    )]
    pub group: AccountLoader<'info, MarginfiGroup>,

    /// The account owning the order
    #[account(
        mut,
        has_one = group @ MarginfiError::InvalidGroup,
        constraint = {
            let acc = marginfi_account.load()?;
            acc.get_flag(ACCOUNT_IN_ORDER_EXECUTION)
                && !acc.get_flag(ACCOUNT_IN_FLASHLOAN)
                && !acc.get_flag(ACCOUNT_FROZEN)
                && !acc.get_flag(ACCOUNT_DISABLED)
                && !acc.get_flag(ACCOUNT_IN_RECEIVERSHIP)
                && !acc.get_flag(ACCOUNT_IN_DELEVERAGE)
        } @MarginfiError::UnexpectedOrderExecutionState
    )]
    pub marginfi_account: AccountLoader<'info, MarginfiAccount>,
```
