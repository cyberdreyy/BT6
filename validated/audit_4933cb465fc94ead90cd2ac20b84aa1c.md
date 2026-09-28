### Title
Lender cannot cancel a write-off request: `fullfillWriteOffRequest` can be front-run into a forced sale at a stale price - ([File: contracts/IdleCreditVaultWriteOffEscrow.sol](contracts/IdleCreditVaultWriteOffEscrow.sol))

### Summary
The external bug is a "last-action-wins" race: `reviewRecipients` lets a later review flip a status that a threshold of earlier reviews had already decided, so the final outcome is decided by tx ordering rather than by the accumulated majority. The analog in idle-tranches is `IdleCreditVaultWriteOffEscrow`: a lender's cancellation (`deleteWriteOffRequest`) and an unprivileged fulfiller's purchase (`fullfillWriteOffRequest`) race in the mempool, and whichever lands last decides whether the lender keeps their tranche tokens or is forced to sell them at a price they may no longer want. There is no expiry, cooldown, or cancellation lock, so a fulfiller can always win the race by front-running.

### Finding Description
`IdleCreditVaultWriteOffEscrow` lets a lender escrow tranche tokens via `createWriteOffRequest(amount, underlyingsRequested)` (IdleCreditVaultWriteOffEscrow.sol:86), cancel and recover them via `deleteWriteOffRequest()` (IdleCreditVaultWriteOffEscrow.sol:105), and lets *any wallet* buy the escrowed tranches via `fullfillWriteOffRequest(_user, _tranches, _underlyings)` (IdleCreditVaultWriteOffEscrow.sol:123), which only requires the fulfiller to pay at least `currentRequest.underlyings` and to request exactly `currentRequest.tranches`.

The asymmetry that creates the race:

- `deleteWriteOffRequest` has no protection at all — it only checks `currentRequest.tranches != 0`.
- `fullfillWriteOffRequest` can be called by any EOA at any time while the request exists.
- Requests have no expiry and no "cancellation pending" state, so the lender's intent to cancel is expressed only by the cancel transaction itself, which is visible in the mempool before it executes.

An unprivileged attacker (e.g., a write-off fulfiller or a trading bot) monitors the mempool for `deleteWriteOffRequest`. When a lender tries to cancel — for example because the request has become stale, the pool is approaching a loss event (e.g., `stopEpochWithDuration` with a loss, or a pending default that would make the tranches worth more than the ask, or simply because market conditions changed) — the attacker front-runs with `fullfillWriteOffRequest`, paying the original `underlyings` and receiving the tranches. The lender's cancel then reverts with `Is0` because their request was already deleted by the fill. Whichever tx lands last wins, exactly like the Allo review race.

The attacker profits the spread between the stale ask and the tranches' true value. Concretely: tranche value is `tranchePrice(tranche)` (or the loss-adjusted/default-recovery value) in underlying units; if `currentRequest.underlyings < _tranches * realPrice / ONE_TRANCHE`, the difference is taken from the lender.

A secondary consequence: the borrower (honest, per the rules) uses the same `fullfillWriteOffRequest` to write off debt via `writeOffDeposit`; a lender can also grief the borrower's fulfill by front-running it with `createWriteOffRequest` adding more tranches/underlyings, which makes the equality/inequality checks in lines 129-131 revert, forcing the borrower to either overpay (`_underlyings >= currentRequest.underlyings`, unbounded above) or abandon the write-off.

### Impact Explanation
Unprivileged fulfillers can force a sale of a lender's escrowed tranche tokens at a stale ask the lender is actively trying to cancel. The loss equals `realTrancheValue - underlyingsPaid`, bounded by the escrowed position size — i.e., a direct transfer of value from lender to fulfiller determined by mempool ordering rather than by the lender's expressed intent. This matches the "outcome decided by a race instead of accumulated state" bug class, and "a user of the write-off fulfiller" is an explicitly allowed attacker role. No privileged party is required.

### Likelihood Explanation
Requires the lender to cancel after their ask becomes favorable to a buyer (market move, tranche price appreciation between request and cancel, or approaching epoch events that change tranche value). Canceling stale orders is a routine operation, and fulfiller bots paying the ask are economically motivated; the attack only needs one standard front-run/back-run. Still lower than a certain-loss bug because it needs a stale, underpriced ask and a pending cancel in the mempool.

### Recommendation
Give requests a cancellation window or expiry so fills cannot race cancels, e.g.:

- Add `uint256 validUntil` (or `requestTimestamp`) to `WriteOffRequest` and revert `fullfillWriteOffRequest` after expiry; let the lender refresh it on create.
- Or add a two-step cancel: `requestCancelWriteOff()` sets a `cancelEffectiveAt` timestamp; `fullfillWriteOffRequest` reverts when `block.timestamp >= cancelEffectiveAt`, and `deleteWriteOffRequest` only executes after it.
- Alternatively allow partial/parametrized fills with a max-price bound set by the fulfiller (`maxUnderlyings`) so a front-run fill cannot exceed a price the buyer chose, and let the lender's cancel take precedence within the same block.

### Proof of Concept
Foundry fork test sketch (in `test/foundry/`, reusing the existing IdleCreditVault/WriteOffEscrow fixtures):

```solidity
function testWriteOffCancelFrontrun() external {
    // epoch running; lender has AA tranche tokens
    address lender = makeAddr("lender");
    address attacker = makeAddr("attacker");
    uint256 tranches = 1000e18;
    uint256 ask = 900e6; // USDC, now underpriced vs current tranche value

    _depositWithUser(lender, 1000e6, true);
    vm.startPrank(lender);
    IERC20(AAtranche).approve(escrow, tranches);
    IdleCreditVaultWriteOffEscrow(escrow).createWriteOffRequest(tranches, ask);
    vm.stopPrank();

    // conditions change: tranche value rises above the ask
    // (e.g., warp through epoch interest accrual, or tranchePrice() increases)

    // lender broadcasts deleteWriteOffRequest(); attacker sees it in mempool
    // and front-runs with fullfillWriteOffRequest paying only the stale ask
    deal(defaultUnderlying, attacker, ask);
    vm.startPrank(attacker);
    IERC20Detailed(defaultUnderlying).approve(escrow, ask);
    IdleCreditVaultWriteOffEscrow(escrow).fullfillWriteOffRequest(lender, tranches, ask);
    vm.stopPrank();

    // lender's cancel now reverts: their request was already consumed
    vm.prank(lender);
    vm.expectRevert(Is0.selector);
    IdleCreditVaultWriteOffEscrow(escrow).deleteWriteOffRequest();

    // attacker holds tranches worth > ask; lender was forced to sell at stale price
    assertEq(IERC20(AAtranche).balanceOf(attacker), tranches);
}
```

Run with `forge test --match-test testWriteOffCancelFrontrun` on a mainnet fork. The assertion demonstrates the race outcome: the fulfiller's fill lands "last" relative to the pending cancel, and the lender's expressed intent to cancel is voided — the same class as `QVBaseStrategy.reviewRecipients` where the last review before the deadline decides the status regardless of accumulated acceptances.