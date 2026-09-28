### Title
Unprivileged instant-withdraw requests can consume all buffer liquidity, leaving the epoch unfunded and forcing a borrower default that socializes losses onto remaining LPs - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
The analog to the reported "oversized position reserves all pool funds → coordinated drain" is the instant-withdraw path in `requestWithdraw`/`startEpoch`. A KYC-passing attacker can deposit a dominant share of the buffer-period liquidity and then request an instant withdrawal when the APR has been lowered (a legitimate trigger condition: `lastEpochApr > unscaledApr + instantWithdrawAprDelta` at `IdleCDOEpochVariant.sol:761-770`). At `startEpoch`, `collectInstantWithdrawFunds` is funded first, and if `pendingInstant > totUnderlyings` the function returns early after sending *all* underlyings to `IdleCreditVault` — the borrower receives nothing for the epoch (`IdleCDOEpochVariant.sol:279-290`). Yet `expectedEpochInterest` was already set from `getContractValue()` net of the instant withdrawals and `pendingWithdraws` still accrues (`IdleCDOEpochVariant.sol:260-282`). At `stopEpoch`, the CDO demands `_amountToPullFromBorrower + _pendingWithdraws` from a borrower that was never funded (`IdleCDOEpochVariant.sol:408`), the honest borrower cannot repay principal/interest it never borrowed, the try/catch falls into `_handleBorrowerDefault` (`IdleCDOEpochVariant.sol:501-505`), and the pool enters the default/finalization flow where remaining LPs take the recovery haircut — while the attacker's instant receipt was already funded at par via `claimInstantWithdrawRequest` (`IdleCreditVault.sol:380-393`).

### Finding Description
- During the buffer phase an attacker (any `isWalletAllowed` user) deposits a large AA/BB position. Buffer deposits sit as contract underlyings until `startEpoch` forwards them.
- When the manager has lowered the APR by more than `instantWithdrawAprDelta`, `requestWithdraw` routes to `creditVault.requestInstantWithdraw(_underlyings, msg.sender)`, burning the attacker's tranche tokens and minting a 1:1 strategy-token receipt (`IdleCDOEpochVariant.sol:761-768`, `IdleCreditVault.sol:356-375`). No fee or penalty applies.
- Because `pendingInstantWithdraws` is satisfied before the borrower is funded, an attacker whose instant requests exceed the contract's underlying balance causes `startEpoch` to send everything to `IdleCreditVault` and return early (`IdleCDOEpochVariant.sol:282-289`), with `sendFundsToBorrower` never called.
- The epoch still runs: `expectedEpochInterest` and `epochEndDate` are set. At `stopEpoch`, the borrower is asked to repay interest plus all normal `pendingWithdraws` despite having received zero principal. Repayment fails → `_handleBorrowerDefault` → `finalizeDefault`/`finalizeDefaultRecovery` haircut for everyone else.
- The attacker exits at par through `claimInstantWithdrawRequest` (enabled by `allowInstantWithdraw = true` after default finalization, `IdleCDOEpochVariant.sol:220`), breaking the loss-waterfall invariant: the first mover escapes at 100% while remaining LPs absorb the default.

### Impact Explanation
Direct loss to all non-exiting LPs: after the forced default, `finalizeDefaultRecovery` prices active tranches and defaulted-epoch receipts at the aggregate recovery multiplier (`IdleCDOEpochVariant.sol:194-225`), so remaining depositors recover only `recoveredAmount / totalBasis` while the attacker claimed full principal. Magnitude is bounded by pool TVL minus the attacker's own exited share; the attack is capital-intensive (the attacker must fund the instant requests), but the profit is avoiding the shared haircut plus extracting liquidity at par.

### Likelihood Explanation
Requires (a) instant withdrawals enabled and (b) an APR reduction exceeding `instantWithdrawAprDelta` — both set by the honest manager, so the trigger is not attacker-controlled. When the trigger condition holds, any single dominant depositor can execute it in two transactions (`requestWithdraw` during buffer, then `claimInstantWithdrawRequest` post-start). The early-return branch in `startEpoch` is explicitly coded, so no revert prevents it. Caveat: if the deployment keeps `disableInstantWithdraw = true` or uses a programmable borrower (`_checkProgrammableBorrowerMode` reverts with pending instant requests, `IdleCDOEpochVariant.sol:105-107`), the attack surface disappears — impact is limited to fixed-APR, non-programmable deployments.

### Recommendation
- In `startEpoch`, when `pendingInstant > totUnderlyings`, do not start a normal epoch accruing borrower interest; either cap the epoch's expected interest/pending-withdraw obligations to what the borrower actually received, or stop the epoch and open claims directly.
- Alternatively, proportionally reduce `expectedEpochInterest` and `pendingWithdraws` obligations by the unfunded fraction, so the borrower is only liable for funds it actually borrowed.
- Consider charging the instant-withdraw path the same upfront fees as normal requests to reduce the incentive to drain buffer liquidity.

### Proof of Concept
```solidity
// Foundry fork PoC (contracts as in repo). Assumes: fixed-APR vault,
// instant withdrawals enabled, manager sets a lower APR (lastEpochApr > apr + delta).
function test_InstantWithdrawDrainsBufferForcesDefault() external {
    // 1. Buffer phase: attacker (KYC'd) dominates deposits; small honest LP deposits too.
    depositAA(ATTACKER, 990_000e6);
    depositAA(HONEST_LP, 10_000e6);

    // 2. Manager (honest) lowers APR by > instantWithdrawAprDelta via stopEpoch/setAprs.

    // 3. During next buffer, attacker requests full withdraw -> instant path.
    vm.prank(ATTACKER);
    cdo.requestWithdraw(0, AAtranche);   // burns tranches, mints instant receipt
    assertGt(strategy.pendingInstantWithdraws(), 0);

    // 4. Manager starts epoch: pendingInstant > contract underlyings ->
    //    all underlyings go to IdleCreditVault, borrower funded with 0.
    vm.prank(manager);
    cdo.startEpoch();
    assertEq(underlying.balanceOf(borrower), borrowerBalBefore); // borrower got nothing

    // 5. Attacker claims instant withdraw at par.
    vm.prank(ATTACKER);
    cdo.claimInstantWithdrawRequest();   // receives full principal

    // 6. Epoch ends; stopEpoch demands interest + pendingWithdraws from borrower.
    vm.warp(cdo.epochEndDate() + 1);
    vm.prank(manager);
    cdo.stopEpoch(newApr, 0);            // borrower cannot repay unfunded amounts
    assertTrue(cdo.defaulted());          // forced default

    // 7. finalizeDefault prices remaining LPs at recovery haircut < par.
    //    ATTACKER exited at 100%; HONEST_LP receives recovery-rate payout only.
}
```

Key code references: `IdleCDOEpochVariant.sol:279-303` (instant funding priority and early return), `IdleCDOEpochVariant.sol:761-770` (instant-withdraw trigger), `IdleCDOEpochVariant.sol:408,501-505` (borrower pull and default catch), `IdleCreditVault.sol:356-393` (instant receipt mint/claim), `IdleCDOEpochVariant.sol:194-225` (`finalizeDefault` haircut).