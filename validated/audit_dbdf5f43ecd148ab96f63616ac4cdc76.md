### Title
`requestInstantWithdraw` bypasses the loss-adjusted receipt guard, letting a user escape a pending-receipt haircut and claim at par - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The external bug is a bounds/overflow check performed *after* the dangerous write — validation ordered too late to protect the state it guards. The analog in `IdleCreditVault` is the loss-epoch receipt guard: `requestWithdraw` enforces that a user holding an unclaimed loss-adjusted (haircutted) receipt must claim it before opening any new request, but `requestInstantWithdraw` performs the burn/mint/receipt writes with no equivalent check at all. An unprivileged tranche holder can therefore route around the haircut and be paid at par, diluting the recovery share of honest users who remain in the loss-adjusted epoch.

### Finding Description
In `requestWithdraw`, before burning/minting, the vault checks whether the user's `lastWithdrawRequest` epoch has a stored `lossRecoveryPrice` and an outstanding receipt (`withdrawsRequestsByEpoch` or an APR0 bucket), and reverts if so:

```solidity
uint256 lossEpoch = lastWithdrawRequest[_user];
uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
if (
  lossRecoveryPrice != 0 &&
  (withdrawsRequestsByEpoch[_user][lossEpoch] != 0 ||
  (apr0Users[_user].principal != 0 && apr0Users[_user].principalEpoch == lossEpoch))
) {
  revert NotAllowed();
}
``` [1](#0-0) 

The comment states the invariant: `lastWithdrawRequest` must keep pointing to the epoch storing the haircut until the receipt is claimed. `requestInstantWithdraw` performs the same kind of state transition — burn CDO-held strategy tokens, mint a user receipt, record per-epoch basis — with no `lossRecoveryPriceByEpoch` / `lastWithdrawRequest` check whatsoever:

```solidity
_burn(msg.sender, _amount);
_mint(_user, _amount);
instantWithdrawsRequests[_user] += _amount;
instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
pendingInstantWithdraws += _amount;
``` [2](#0-1) 

When `collectWithdrawFunds` is called at `stopEpoch` with `_amount < pendingWithdraws`, it stores `lossRecoveryPriceByEpoch[epochNumber]` and zeroes `pendingWithdraws`, so every pending receipt in that epoch is only redeemable at the haircut price. [3](#0-2)  But `claimInstantWithdrawRequest` pays `instantWithdrawsRequests[_user]` in full via `_transferFundedClaim`, independent of any loss-epoch haircut. [4](#0-3) 

Attack sequence (APR0 or normal mode, running epoch):
1. Attacker (KYC'd tranche holder) calls `requestWithdraw` through the CDO in epoch N → pending receipt recorded.
2. `stopEpoch` realizes a loss → `collectWithdrawFunds` funds less than `pendingWithdraws` → `lossRecoveryPriceByEpoch[N] < 1e18`. Honest users' receipts are now worth, e.g., 70%.
3. Instead of claiming the haircutted receipt, the attacker calls `requestInstantWithdraw` (epoch N+1 buffer/running phase) for the same tranche position. No guard stops them; `instantWithdrawsRequests[attacker]` is now a fresh full-par receipt, and `instantWithdrawClaimsByEpoch` is inflated accordingly.
4. Manager's `getInstantWithdrawFunds` / `collectInstantWithdrawFunds` funds instant requests (honest privileged sequencing); attacker calls `claimInstantWithdrawRequest` and receives 100% of `_amount`.
5. The attacker's old receipt still sits in `withdrawsRequestsByEpoch[N]` — the loss-adjusted bucket is under-claimed while the attacker extracted par value funded by the shared recovery pool.

The broken invariant is the loss waterfall / "one receipt one payout" rule: a loss-epoch receipt must be claimed at its haircut before the user opens new claims. The guard exists but is ordered/enforced only on the normal-request path — the exact "check placed where it can no longer prevent the bad write" pattern of the kernel bug.

### Impact Explanation
Direct theft from other withdraw requesters. The recovery pool funded at `stopEpoch` is sized for `pendingWithdraws` receipts at `lossRecoveryPrice`. An attacker who converts their haircutted exposure into a par-paid instant receipt extracts more than their pro-rata share, permanently reducing what honest loss-epoch receipt holders recover. Loss is quantified as `attackerReceipt * (1 - lossRecoveryPrice)` per attacker, bounded only by their tranche position, and repeatable across every loss epoch.

### Likelihood Explanation
Requires a `stopEpochWithDuration`-style partial loss (`collectWithdrawFunds` with `_amount < pendingWithdraws`) and an instant-withdraw facility enabled — both are supported modes. The attacker needs only to be a tranche-token holder requesting through the CDO in the next epoch; all privileged callers (manager funding, owner stopping the epoch) act honestly. The only uncertainty is whether `IdleCDOEpochVariant`'s instant-withdraw entry point independently blocks users with pending normal requests — the vault-level code shown has no such check, so unless the CDO duplicates it, the path is open.

### Recommendation
Apply the same precondition in `requestInstantWithdraw` before the burn/mint writes: revert if `lossRecoveryPriceByEpoch[lastWithdrawRequest[_user]] != 0` and the user holds a receipt (normal or APR0) in that loss epoch. Mirror the `requestWithdraw` ordering — validate the stale loss-epoch state first, then mutate. Optionally also block instant requests while `postDefaultRequests[_user] != 0` for consistency with the post-default path.

### Proof of Concept
Foundry fork test (sketch — fixture follows `test/foundry/IdleCreditVault.t.sol` helpers `_startEpochAndCheckPrices`, `idleCDO.depositAA/BB`):

```solidity
function testInstantWithdrawSkipsLossHaircut() public {
  // Epoch N: attacker + victim deposit and both requestWithdraw
  idleCDO.depositAA(100_000e18);           // victim
  vm.startPrank(attacker);
  // attacker deposits via CDO and requests withdraw of e.g. 10_000e18
  idleCDO.depositAA(10_000e18);
  vm.stopPrank();

  // both file withdraw requests in epoch N (buffer phase)
  cdoEpoch.requestWithdrawFunds(victimAmount);   // via user flows
  cdoEpoch.requestWithdrawFunds(attackerAmount);

  // stopEpoch realizes a loss -> collectWithdrawFunds funds only 70%
  // lossRecoveryPriceByEpoch[N] = 0.7e18
  stopEpochWithLoss(...);

  // Invariant guard blocks re-request on normal path:
  vm.expectRevert(NotAllowed.selector);
  cdoEpoch.requestWithdrawFunds(attackerAmount); // reverts as designed

  // But instant path has no check:
  cdoEpoch.requestInstantWithdraw(attackerAmount); // succeeds
  // manager funds instant requests honestly
  getInstantWithdrawFunds(attackerAmount);

  uint256 balBefore = underlying.balanceOf(attacker);
  cdoEpoch.claimInstantWithdrawRequest(attacker);
  // attacker received 100% par while lossRecoveryPrice = 0.7e18
  assertEq(underlying.balanceOf(attacker) - balBefore, attackerAmount);
  // victim can only claim 0.7 * victimAmount from the same loss epoch
}
```

Note: the PoC depends on the CDO's `requestInstantWithdraw` entrypoint forwarding without its own stale-receipt check; this was not fully verified against `IdleCDOEpochVariant.sol` within this review. If the CDO already blocks instant requests for users with pending loss-epoch receipts, the vault-level gap is defense-in-depth rather than exploitable, and severity drops to a hardening recommendation.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L261-271)
```text
    uint256 lossEpoch = lastWithdrawRequest[_user];
    uint256 lossRecoveryPrice = lossRecoveryPriceByEpoch[lossEpoch];
    if (
      lossRecoveryPrice != 0 &&
      (withdrawsRequestsByEpoch[_user][lossEpoch] != 0 ||
      (apr0Users[_user].principal != 0 && apr0Users[_user].principalEpoch == lossEpoch))
    ) {
      // A loss-adjusted receipt must be claimed before opening a later request, otherwise
      // `lastWithdrawRequest` would stop pointing to the epoch that stores its haircut.
      revert NotAllowed();
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L356-375)
```text
  function requestInstantWithdraw(uint256 _amount, address _user) external {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();
    // burn strategy tokens from cdo
    _burn(msg.sender, _amount);
  
    // mint equal amount of strategy tokens to the user as receipt, useful in case of default
    _mint(_user, _amount);

    // increase the instant withdraw requests for the user
    instantWithdrawsRequests[_user] += _amount;
    uint256 currentEpoch = epochNumber;
    // we record both per-user (old, kept for compatibility) and per-epoch so on
    // finalization we can distinguish "default-epoch pending instant receipts"
    // from old funded instant receipts.
    instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
    // increase the total instant withdraw requests
    pendingInstantWithdraws += _amount;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L380-393)
```text
  function claimInstantWithdrawRequest(address _user) external {
    _onlyIdleCDO();
    if (defaultRecoveryFinalized && defaultInstantWithdrawsFinalized) {
      // Clear the defaulted-epoch instant receipt first, then continue so the same call can
      // also pay any older instant receipt that was already funded before default finalization.
      _claimDefaultedInstantWithdrawRequest(_user);
    }
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-430)
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
  }
```
