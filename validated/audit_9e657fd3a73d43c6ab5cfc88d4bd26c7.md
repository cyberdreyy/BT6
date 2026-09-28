### Title
`createWriteOffRequest` overflows `pendingUnderlyings`, permanently DoSing new write-off requests - (File: contracts/IdleCreditVaultWriteOffEscrow.sol)

### Summary
`IdleCreditVaultWriteOffEscrow.createWriteOffRequest` lets any tranche holder supply an arbitrary `underlyingsRequested` amount that is added to the global `pendingUnderlyings` counter with no bound. An attacker can push `pendingUnderlyings` to `type(uint256).max` at no capital cost, after which every subsequent `createWriteOffRequest` call with a nonzero requested amount reverts on overflow. Notably, the decrement paths already implement the saturating pattern that the referenced audit report recommends — `deleteWriteOffRequest` and `fullfillWriteOffRequest` clamp with `pendingUnderlyings >= currentRequest.underlyings ? currentRequest.underlyings : pendingUnderlyings` — while the increment path remains unprotected.

### Finding Description
In `createWriteOffRequest` (lines 86-102), the contract pulls `amount` tranche tokens from the caller and updates two counters:

- `userRequests[msg.sender].underlyings += underlyingsRequested`
- `pendingUnderlyings += underlyingsRequested`

`underlyingsRequested` is entirely attacker-controlled and unvalidated. Passing `underlyingsRequested = type(uint256).max - pendingUnderlyings + 1` (or simply `type(uint256).max` on a fresh escrow) drives `pendingUnderlyings` to the uint256 ceiling. From then on, any honest lender calling `createWriteOffRequest(amount, underlyingsRequested > 0)` hits an arithmetic overflow in `pendingUnderlyings += underlyingsRequested` and reverts.

The asymmetry mirrors the Olympus finding exactly: `decrease`/`withdraw`-style paths use clamping (`deleteWriteOffRequest` line 112, `fullfillWriteOffRequest` line 134), but the `increase`-style path has no equivalent handling, and unlike the Olympus case the overflow is reachable from a single unprivileged call rather than requiring the counter to organically reach max.

### Impact Explanation
- Temporary freezing of a user-facing mechanism: while the attacker's request stands, no lender can create a new write-off request with a nonzero ask. `pendingUnderlyings` only decreases via `deleteWriteOffRequest`/`fullfillWriteOffRequest` on the offending request, and only the attacker (or a fulfiller paying the absurd `underlyings` amount) can trigger that.
- The attacker retains the deposited tranche tokens and can withdraw them via `deleteWriteOffRequest` at any time, making the grief repeatable and capital-free (a single wei's worth of tranche tokens suffices as `amount`).
- Practical effect: during a stressed epoch — precisely when lenders would use the escrow to exit distressed debt — the exit/negotiation venue can be held shut continuously by re-poisoning `pendingUnderlyings` after each deletion, impairing lenders' ability to recover value on demand.

### Likelihood Explanation
- Attacker profile: any tranche-token holder (in-scope role); the only cost is a token transfer that is fully refundable.
- Preconditions: the epoch must be running (`isEpochRunning()`), which is the normal state; the escrow needs no special configuration.
- No existing guard stops it: `nonReentrant` is irrelevant, `amount == 0` check doesn't constrain `underlyingsRequested`, and there is no cap tying `underlyingsRequested` to deposited tranche value or remaining debt.

### Recommendation
Validate `underlyingsRequested` against a sensible bound (e.g., tranche deposit value or outstanding debt), and/or apply saturating accounting symmetric to the decrement path:

```solidity
// contracts/IdleCreditVaultWriteOffEscrow.sol
pendingUnderlyings = type(uint256).max - pendingUnderlyings <= underlyingsRequested
    ? type(uint256).max
    : pendingUnderlyings + underlyingsRequested;
```

Capping `underlyingsRequested` (e.g., `<= amount` scaled by price, or a protocol-defined max) is preferable since it also prevents nonsensical requests.

### Proof of Concept
Foundry test sketch (fork setup follows the repo's existing credit-vault test harness):

```solidity
function testPendingUnderlyingsOverflowDoS() public {
    // epoch running; escrow = deployed IdleCreditVaultWriteOffEscrow
    deal(address(tranche), attacker, 1);
    deal(address(tranche), lender, 100e18);

    // attacker poisons the global counter
    vm.startPrank(attacker);
    tranche.approve(address(escrow), 1);
    escrow.createWriteOffRequest(1, type(uint256).max);
    vm.stopPrank();
    assertEq(escrow.pendingUnderlyings(), type(uint256).max);

    // honest lender can no longer create a request
    vm.startPrank(lender);
    tranche.approve(address(escrow), 100e18);
    vm.expectRevert(); // arithmetic overflow on pendingUnderlyings +=
    escrow.createWriteOffRequest(100e18, 50e18);
    vm.stopPrank();

    // attacker can restore functionality and recover tokens at will -> repeatable
    vm.prank(attacker);
    escrow.deleteWriteOffRequest();
}
```

Uncertainty: I verified `pendingUnderlyings` accounting only within `IdleCreditVaultWriteOffEscrow.sol`; if the strategy reads `pendingUnderlyings` elsewhere for solvency checks, impact could be higher than a DoS. I was unable to confirm all external readers within the iteration budget.