### Title
Epoch-wait guard in `claimWithdrawRequest` is bypassed before the first epoch (and whenever `epochEndDate == 0`), letting a lender claim unearned interest paid from other depositors' buffered funds - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
The picklescan bug class is a *scanner/filter miss*: a denylist that blocks known dangerous gadgets but lets an equivalent one through. The analog is the same-epoch claim filter in `IdleCreditVault._claimFundedWithdrawRequest`. The "user must wait one epoch" check is only applied when `IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0`. Because `epochEndDate` is `0` before the very first `startEpoch` (and permanently after a `_interest == 1` pool close or `finalizeDefault`), a withdraw request made in that state is exempt from the freshness check and can be claimed immediately — before any `stopEpoch` has funded it via `collectWithdrawFunds`. The claim is paid out of the raw underlying already sitting in the strategy contract (buffered deposits earmarked for the first epoch), so the attacker extracts other users' principal plus a full unearned epoch of interest.

### Finding Description
`IdleCDOEpochVariant.requestWithdraw` lets any Keyring-allowed wallet burn tranche tokens and register a receipt for `principal + interest - fees` in `IdleCreditVault` (`pendingWithdraws` / `withdrawsRequests[_user]`), even during the buffer period before the first epoch. `claimWithdrawRequest` on the CDO forwards to `IdleCreditVault.claimWithdrawRequest(_user)`, which falls through to `_claimFundedWithdrawRequest`: [1](#0-0) 

The `epochNumber <= lastWithdrawRequest[_user]` revert is the only thing preventing a same-epoch claim, and it is gated on `epochEndDate() != 0`. When the CDO has `epochEndDate == 0` — true on a fresh deployment before the first `startEpoch`, and after `finalizeDefault`/`close-pool` set it to `0` — the guard is skipped entirely. The function then settles the full recorded amount (principal *plus* interest minus fees) via `_transferFundedClaim`, which pays from the strategy's actual underlying balance rather than from funds pulled through `collectWithdrawFunds` at `stopEpoch`: [2](#0-1) 

Meanwhile, buffer-period deposits are pushed into the strategy via `deposit()` (`totEpochDeposits`), so the strategy holds spendable underlying belonging to *other* depositors. In `stopEpoch`, the design intent is clear: withdraw receipts are only paid after `getFundsFromBorrower` + `collectWithdrawFunds` move borrower repayment into the vault: [3](#0-2) 

The `epochEndDate == 0` exemption exists to make close-pool claims immediate (`epochDuration = 0; epochEndDate = 0` in `_stopEpoch`), but it also silently covers the pre-first-epoch state — the "missed gadget" equivalent — where no epoch has ever run and no funding step has occurred.

### Impact Explanation
An attacker who passes Keyring KYC can deposit `X`, immediately `requestWithdraw` the full tranche balance (receipt = `X + interest − fees` where `interest` is a full epoch+buffer of yield computed by `_calcInterestWithdrawRequest`), then call `claimWithdrawRequest` in the same state. Because the freshness guard is skipped at `epochEndDate == 0`, the strategy pays the receipt from its underlying balance, which consists of buffered deposits from honest LPs. Net effect: the attacker withdraws more than deposited — the unearned interest component is a direct theft of other depositors' principal, and if `X` is large relative to buffered TVL the payout also shortfalls later claimants (insolvency of the pending-withdraw bucket). Loss quantification: up to `interest = X * apr * (epochDuration + bufferPeriod) / 365 days` plus any other users' principal consumed, bounded by the strategy's underlying balance.

### Likelihood Explanation
Requirements: a pool freshly deployed and still in its initial buffer period (`epochEndDate == 0`, deposits open, `allowAAWithdrawRequest` true), at least one honest depositor's funds already sitting in the strategy, and a Keyring-passing attacker. No privileged cooperation, timing race, or oracle manipulation is needed — two ordinary user calls (`requestWithdraw`, `claimWithdrawRequest`) suffice, and both are exactly the functions the rules put in scope. The main caveat: if a deployment configures `epochEndDate` non-zero or `allowAAWithdrawRequest` false at init, the window narrows to the post-close/`finalizeDefault` states, where a *new* `requestWithdraw` (still permitted since `allowAAWithdrawRequest`/`allowBBWithdrawRequest` are re-enabled) can likewise be claimed without ever passing through `collectWithdrawFunds`, draining the default recovery reserve or other users' funded claims ahead of legitimate recipients. The invariant broken is "one receipt, one funded payout": receipts are paid without the corresponding funding step.

### Recommendation
In `IdleCreditVault._claimFundedWithdrawRequest`, do not exempt claims based solely on `epochEndDate == 0`. Instead, only skip the `epochNumber <= lastWithdrawRequest[_user]` check when the pool is actually closed or finalized (e.g., `epochDuration == 0` after a `_interest == 1` stop, or `defaultRecoveryFinalized`), which the strategy can verify via the CDO, and revert otherwise — including the pre-first-epoch state. Alternatively, track funded-vs-unfunded receipt balances explicitly (funded only by `collectWithdrawFunds` / the default recovery reserve) and pay claims only from the funded bucket, which removes reliance on the `epochEndDate` sentinel entirely.

### Proof of Concept
Foundry fork PoC (test contract against the repo's own deployment helpers, modeled on `test/foundry/IdleCreditVault.t.sol`):

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "test/foundry/IdleCreditVault.t.sol";

contract ClaimBypassPreFirstEpochPoC is IdleCreditVaultTest {
    function testClaimWithdrawBeforeFirstEpochStealsInterest() external {
        uint256 amount = 100_000 * ONE_SCALE;
        address attacker = makeAddr("attacker");

        // Fresh pool: buffer period, epochEndDate == 0, no epoch has ever run.
        assertEq(cdoEpoch.epochEndDate(), 0);

        // Honest LP deposits; funds are pushed to the strategy (totEpochDeposits).
        _depositWithUser(makeAddr("honestLP"), amount, true);

        // Attacker is Keyring-allowed and deposits too.
        _depositWithUser(attacker, amount, true);

        // Attacker requests a full withdraw: receipt = principal + interest - fees,
        // where interest is a full epoch+buffer of unearned yield.
        vm.prank(attacker);
        uint256 receipt = cdoEpoch.requestWithdraw(0, address(AAtranche));
        assertGt(receipt, amount, "receipt includes unearned interest");

        // Guard bypass: epochEndDate == 0 skips `epochNumber <= lastWithdrawRequest`,
        // so the unfunded receipt is paid from the strategy's underlying balance
        // (honest LP's buffered deposit), without any stopEpoch/collectWithdrawFunds.
        uint256 balPre = underlying.balanceOf(attacker);
        vm.prank(attacker);
        cdoEpoch.claimWithdrawRequest();
        uint256 payout = underlying.balanceOf(attacker) - balPre;

        assertEq(payout, receipt, "unfunded receipt paid immediately");
        assertGt(payout, amount, "attacker withdrew more than deposited");

        // The excess was drained from funds belonging to the honest LP,
        // whose tranche NAV is now under-backed (insolvency of buffered deposits).
        assertLt(
            underlying.balanceOf(address(strategy)),
            amount, // only the honest LP's buffered deposit remains, partially consumed
            "strategy balance should be drained below honest deposit"
        );
    }
}
```

Steps: deploy the standard `IdleCreditVault`/`IdleCDOEpochVariant` fixture, stay in the initial buffer period (never call `startEpoch`), have one honest LP deposit, then the attacker deposits → `requestWithdraw(0, AATranche)` → `claimWithdrawRequest()` in the same block/epoch. The attacker receives `principal + interest − fees` paid from the honest LP's buffered deposit, demonstrating theft proportional to one epoch of APR on the attacker's principal.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L326-328)
```text
    if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
      revert NotAllowed();
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L338-349)
```text
    uint256 normalAmount = withdrawsRequests[_user];
    uint256 apr0PrincipalAmount = _apr0User.settledPrincipal + _apr0User.principal;
    uint256 apr0InterestAmount = _apr0User.settledInterest;
    amount = normalAmount + apr0PrincipalAmount + apr0InterestAmount;
    // burn strategy tokens 1:1 with the principal only (normal amount already includes interest)
    _burn(_user, normalAmount + apr0PrincipalAmount);
    withdrawsRequests[_user] = 0;
    lastWithdrawRequest[_user] = 0;
    if (apr0PrincipalAmount != 0 || apr0InterestAmount != 0) {
      delete apr0Users[_user];
    }
    _transferFundedClaim(_user, amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-429)
```text
  function collectWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    uint256 pendingBasis = pendingWithdraws;
    if (_amount < pendingBasis) {
      // Legacy receipts do not have per-epoch ownership data, so they can only be fully funded.
      if (!defaultRecoveryInitialized) revert NotAllowed();
      uint256 lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis;
      // Avoid storing a zero price, which is indistinguishable from "no loss-adjusted epoch".
      if (lossRecoveryPrice == 0) revert NotAllowed();
      pendingWithdraws = 0;
      lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice;
    } else {
      // A plain implementation upgrade may leave legacy normal receipts pending. Their next
      // successful stop can fully fund the aggregate before lazy initialization occurs.
      pendingWithdraws = pendingBasis - _amount;
    }
    if (_amount != 0) {
      underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
    }
```
