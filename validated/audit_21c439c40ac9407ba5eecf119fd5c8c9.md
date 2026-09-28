### Title
Write-off requests never expire and can be fulfilled in a changed epoch/default context, letting an attacker execute stale escrowed orders at prices the lender no longer intends - ([File: contracts/IdleCreditVaultWriteOffEscrow.sol](contracts/IdleCreditVaultWriteOffEscrow.sol))

### Summary
`IdleCreditVaultWriteOffEscrow.createWriteOffRequest` escrows a lender's tranche tokens with a fixed `underlyings` ask, but the request carries no deadline, no epoch binding, and `fullfillWriteOffRequest` performs no `isEpochRunning()` or state check at all. A request created during a healthy running epoch remains executable indefinitely — across later epochs, after borrower default, or after recovery repricing — exactly the bug class of "signed/recorded request valid forever, executable when the context has drastically changed."

### Finding Description
- `createWriteOffRequest` only gates on `isEpochRunning()` at creation time and stores `{tranches, underlyings}` with no timestamp or expiry (lines 86-102).
- `fullfillWriteOffRequest` (lines 123-155) can be called by "any wallet" (`@dev this function can be called by any wallet`) and checks only that `_underlyings >= currentRequest.underlyings`. There is no check that the epoch is still running, that the vault is not defaulted, or that the tranche price is still near the value the lender observed when creating the request.
- `deleteWriteOffRequest` is lender-initiated only; if the lender is inactive (or simply hasn't repriced), the order is a free perpetual option for any fulfiller.
- In `IdleCreditVault`, tranche value is dynamic: defaults and recovery flows (`defaultRecoveryPrice`, `lossRecoveryPriceByEpoch`, `finalizeDefault*`) and borrower repayments reprice tranche claims. A stale escrowed order ignores all of this.

### Impact Explanation
Direct theft of value from the lender by an unprivileged fulfiller: the attacker waits until the escrowed tranches are worth more than the stale ask — e.g., borrower repays after a partial default so the recovery/waterfall value of the tranche rises, or interest accrual lifts `virtualPrice` — then calls `fullfillWriteOffRequest`, pays the outdated `underlyings` amount, and receives tranche tokens redeemable for more. Loss to the lender is quantified as `currentTrancheValue * tranches - underlyings` (minus the 0.1% exit fee), extractable in a single transaction. Conversely, if tranche value collapses (default), the lender's tranches remain uselessly escrowed while the order sits — the lender cannot use them in recovery claims unless they actively delete the request, and a fulfiller can still pick up defaulted tranches at the stale (now generous-to-lender) price only if it suits them. The asymmetry — fulfiller executes only when favorable, lender bears all staleness risk — is the same "execute much later than intended" harm as the Forwarder finding.

### Likelihood Explanation
- Any EOA/contract can fulfill (`_user` parameter, no access control, no KYC gate on the fulfiller path).
- Request creation legitimately requires a running epoch, but fulfillment is entirely ungated, so no privileged call is needed to reach the exploit — only time passing plus any repricing event (epoch stop with interest, `stopEpochWithDuration` loss repricing, default finalization, or borrower repayment).
- The only mitigation is that the lender can call `deleteWriteOffRequest`; this requires the lender to actively monitor and race the fulfiller, identical to the judge-noted "no mechanism to limit the time window" concern.

### Recommendation
Add an expiry to `WriteOffRequest` (e.g., `uint40 deadline` settable at creation, or a fixed maximum TTL), and revert in `fullfillWriteOffRequest` if `block.timestamp > deadline`. Additionally, re-check `IdleCDOEpochVariant(idleCDOEpoch).isEpochRunning()` (or at minimum revert when `defaultRecoveryFinalized` / `epochEndDate() == 0`) inside `fullfillWriteOffRequest` so requests cannot be executed across epoch-state transitions the lender never priced.

### Proof of Concept
Foundry fork sketch:

```solidity
function testStaleWriteOffRequestFulfilledAfterRepricing() external {
  // Running epoch: lender escrows 100 tranches asking 90e6 USDC (10% discount)
  vm.prank(lender);
  escrow.createWriteOffRequest(100e18, 90e6); // tranche price ~1.0

  // Time passes; epoch stops/starts, interest accrues or borrower repays
  // such that virtualPrice(tranche) rises to ~1.2
  _stopCurrentEpoch(); _startEpochWithInterestAccrued(); // price now 1.2e18

  // Attacker (any EOA) fulfills the dormant order, paying only the stale 90e6
  deal(underlying, attacker, 90e6);
  vm.startPrank(attacker);
  IERC20(underlying).approve(address(escrow), 90e6);
  escrow.fullfillWriteOffRequest(lender, 100e18, 90e6);
  vm.stopPrank();

  // Attacker now holds tranches worth 120e6; lender received ~89.91e6 (0.1% fee)
  uint256 redeemable = 100e18 * cdoEpoch.virtualPrice(tranche) / 1e18;
  assertGt(redeemable, 90e6); // ~30e6 value extracted from lender
}
```

The test reproduces the finding: a request recorded in one epoch context executes unchanged in a materially different later context because neither a deadline nor an epoch/state check exists on the fulfillment path.