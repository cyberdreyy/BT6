### Title
Withdraw requests made after pool close are instantly claimable and drain funded reserves of earlier withdrawers - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The upstream bug is a data race between `Retrieve` and `Close`: a read/claim executed while (or after) a close observes stale state. The analog in `IdleCreditVault` is a request-then-claim race against the pool-closed state: when `epochEndDate == 0`, `requestWithdraw` still mints a full-value receipt but deliberately skips adding to `pendingWithdraws`, and `_claimFundedWithdrawRequest` skips the mandatory one-epoch wait. A post-close requester can therefore redeem instantly at par against cash that was collected at close only to back earlier pending receipts, stealing unfunded underlying from prior claimants.

### Finding Description
In `requestWithdraw`, when the CDO pool is closed (`IIdleCDOEpochVariant(idleCDO).epochEndDate() == 0`), the strategy still burns the requester's principal receipt and mints `_amount` of strategy tokens to the user, but skips `pendingWithdraws += _amount` (`contracts/strategies/idle/IdleCreditVault.sol:259-294`). The rationale is that a closed pool has no future `stopEpoch` to source funds — which is precisely why the new receipt is *unfunded*.

The claim-side guard in `_claimFundedWithdrawRequest` then fails open for exactly this case:

```solidity
if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
  revert NotAllowed();
}
```

(`contracts/strategies/idle/IdleCreditVault.sol:326-328`). When the pool is closed, `epochEndDate() == 0`, so the epoch-wait check is bypassed entirely. `amount` then includes the just-created `withdrawsRequests[_user]` entry (the closed-pool path still writes `withdrawsRequests`/`withdrawsRequestsByEpoch` at lines 292-293), and `_transferFundedClaim` pays it from the strategy's underlying balance.

At close (`stopEpoch(0, 1)`), `collectWithdrawFunds` transferred exactly `pendingWithdraws` underlying into the strategy to back receipts of users who requested *before* close. Those funds are fungible with any residual strategy balance. `_transferFundedClaim` only protects `defaultRecoveryReserve` — which is zero for a clean, non-defaulted close — so nothing isolates the prior claimants' cash (`contracts/strategies/idle/IdleCreditVault.sol:897-907`).

Attack sequence (pool in closed state, `epochEndDate == 0`, no default):

1. Attacker, an ordinary AA/BB tranche holder, calls `IdleCDOEpochVariant.requestWithdraw(amount, tranche)`. `IdleCreditVault.requestWithdraw` burns `_principal`, mints receipt tokens to the attacker, records `withdrawsRequests[attacker] += amount`, but does not increase `pendingWithdraws`.
2. Attacker calls `claimWithdrawRequest`. The epoch gate is skipped because `epochEndDate == 0`; `_claimFundedWithdrawRequest` burns the receipt and transfers `amount` underlying from the strategy balance.
3. The transferred cash was funded at close for earlier requesters. Each earlier claimant who calls `claimWithdrawRequest` afterward either receives less or reverts on insufficient balance — a direct transfer of their funded claim to the attacker.

The mint is not isolated by any funded-vs-unfunded accounting: `withdrawsRequests` mixes funded pre-close receipts and unfunded post-close receipts in one aggregate, so the unfunded request is indistinguishable at claim time.

### Impact Explanation
Direct theft. The attacker receives underlying equal to their requested amount without any borrower repayment ever being sourced for it (closed pool ⇒ no `stopEpoch` ⇒ no `collectWithdrawFunds`). The loss is borne by pre-close withdraw requesters whose funded reserves are drained, up to the full residual strategy balance backing unclaimed receipts. With a pool that closed with, e.g., 500k USDC of unclaimed pending receipts, a tranche holder can request and immediately claim that amount for the price of tranche tokens that are burned anyway.

### Likelihood Explanation
Requires the pool to be in the closed state (`epochEndDate == 0`) with funded-but-unclaimed withdraw receipts in the strategy — a normal end-of-life condition for these credit vaults, explicitly supported by the code (the `isClosed` branch and comments such as "claims can be immediate"). Any tranche-token holder qualifies as attacker; no privileged role is needed since `requestWithdraw`/`claimWithdrawRequest` are user-facing. The window persists as long as unclaimed funded receipts remain.

### Recommendation
- Revert in `IdleCreditVault.requestWithdraw` (or in `IdleCDOEpochVariant.requestWithdraw`) when `epochEndDate() == 0`, so no new receipts can be created against a pool that will never fund them; or
- If post-close requests must remain supported, keep the one-epoch gate meaningful by tracking closed-pool requests separately and never paying them from funded reserves, e.g. require `lastWithdrawRequest` to have been set in an epoch strictly below the close epoch before honoring the claim.

### Proof of Concept
Foundry fork PoC (setup mirrors `test/foundry/IdleCreditVault.t.sol` close-pool tests; `cdoEpoch`, `strategy`, `underlying`, `idleCDO`, `borrower`, `manager`, `aaTranche` from that harness):

```solidity
function testPostCloseRequestDrainsFundedReceipts() external {
  uint256 amount = 10_000 * ONE_SCALE;
  // userA deposits and files a normal withdraw request in the buffer
  vm.prank(userA);
  idleCDO.depositAA(amount);
  // attacker also holds tranche tokens
  vm.prank(attacker);
  idleCDO.depositAA(amount);

  vm.prank(userA);
  cdoEpoch.requestWithdraw(amount / 2, address(aaTranche)); // pendingWithdraws = amount/2

  _startEpochAndCheckPrices(0);

  // borrower repays everything; manager closes the pool
  deal(defaultUnderlying, borrower, strategy.balanceOf(address(cdoEpoch)) + strategy.pendingWithdraws());
  vm.warp(cdoEpoch.epochEndDate() + 1);
  vm.prank(manager);
  cdoEpoch.stopEpoch(0, 1);                       // epochEndDate == 0, receipts funded

  uint256 stratBal = underlying.balanceOf(address(strategy));
  assertEq(stratBal, amount / 2);                 // cash reserved for userA's receipt

  // attacker requests AFTER close: receipt minted, pendingWithdraws untouched
  vm.prank(attacker);
  cdoEpoch.requestWithdraw(amount / 2, address(aaTranche));

  // claim succeeds immediately because epochEndDate() == 0 skips the wait gate
  vm.prank(attacker);
  cdoEpoch.claimWithdrawRequest();

  assertEq(underlying.balanceOf(attacker), amount / 2);   // attacker paid at par
  // userA's funded claim is now unbacked
  vm.prank(userA);
  vm.expectRevert();                              // insufficient strategy balance
  cdoEpoch.claimWithdrawRequest();
}
```

Uncertainty note: this assumes `IdleCDOEpochVariant.requestWithdraw` is not gated off when the pool is closed (the `isClosed` branch in `requestWithdraw` strongly implies post-close requests are intended to be reachable). If the CDO-level entry point already reverts post-close for normal requests, the entry path needs re-verification, but the claim-gate bypass (`epochEndDate() != 0` short-circuit) combined with unfunded receipt minting remains the vulnerable logic.