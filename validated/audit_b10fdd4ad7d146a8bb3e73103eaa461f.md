### Title
Unfunded instant-withdraw receipts from a pre-default epoch escape the recovery haircut and are paid at par - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The bug class in CVE-2020-13498 — an out-of-bounds read driven by a mismatched index/type — maps here to the epoch-keyed instant-withdraw ledgers in `IdleCreditVault`. Default finalization computes the recovery basis and prefunded reserve using only `instantWithdrawClaimsByEpoch[epochNumber]` (the *current* epoch), while the claim path pays out the *aggregate* `instantWithdrawsRequests[user]` (all epochs). An instant receipt created in an earlier epoch that was never funded escapes the recovery haircut entirely, yet is still paid 1:1 from the strategy — draining funds owed to defaulted-epoch claimants.

### Finding Description
`defaultPendingClaimBasis()` adds only `instantWithdrawClaimsByEpoch[epochNumber]` to the default claim basis, i.e., instant receipts requested in the epoch in which finalization occurs. [1](#0-0)  Likewise `_defaultPrefundedInstantReserve()` computes `instantBasis - pendingInstant` using only the current epoch's claim total. [2](#0-1) 

However `pendingInstantWithdraws` is a global, epoch-agnostic counter incremented in `requestInstantWithdraw` and only decremented in `collectInstantWithdrawFunds`. [3](#0-2)  The comments at lines 637-640 confirm it can remain non-zero across epochs (startEpoch moved only partial cash to the strategy). Nothing removes an unfunded instant receipt when `epochNumber` rolls over via `deposit()`. [4](#0-3) 

At claim time, `_claimDefaultedInstantWithdrawRequest` clears only `instantWithdrawsRequestsByEpoch[user][defaultEpoch]`; the remainder of `instantWithdrawsRequests[user]` — including older, still-unfunded receipts — is then burned and paid in full via `_transferFundedClaim`. [5](#0-4) [6](#0-5) 

The stale-index reads compound:

- `totalBasis` in `finalizeDefaultRecovery` excludes the old-epoch unfunded instant amount, so `defaultRecoveryPrice` is computed too high and `defaultRecoveryReserve` is under-provisioned. [7](#0-6) 
- `_defaultPrefundedInstantReserve` misattributes the global `pendingInstantWithdraws` gap entirely to the current epoch; if the old unfunded amount exceeds the current epoch's basis, `prefundedReserve` clamps to 0 and the shortfall is silently pushed into the reserve math.

### Impact Explanation
Broken invariant: one recovery pie, proportional payout. An unprivileged lender whose instant request was left unfunded in epoch N (e.g., the CDO had insufficient liquid cash at `getInstantWithdrawFunds` and the deficit rolled into the next epoch) receives 100 cents on the dollar after default finalization, while every defaulted-epoch receipt holder and active LP is haircut at `defaultRecoveryPrice`. The payment comes out of strategy underlying that was reserved for `defaultRecoveryReserve` or other funded claims, directly reducing what honest claimants can withdraw — direct theft of recovery funds, quantifiable as the full unfunded old-epoch instant amount times `(1 - recoveryPrice)` plus dilution of all other claims.

### Likelihood Explanation
Requires only: (a) an instant withdrawal that stays partially unfunded across an `epochNumber` rollover — a state the code explicitly supports since `pendingInstantWithdraws` is not epoch-reset and prefunding can be partial; (b) a subsequent borrower default finalized via `finalizeDefault`. Both are normal protocol flows reachable by an ordinary KYC'd lender; the attacker merely times a `requestInstantWithdraw` before an epoch where liquidity is thin. No privileged collusion is needed.

### Recommendation
Key the unfunded instant bucket by epoch: either roll `instantWithdrawClaimsByEpoch` forward into the new `epochNumber` at `startEpoch`/`stopEpoch`, or include the full `pendingInstantWithdraws`-backed unfunded basis (not just `instantWithdrawClaimsByEpoch[epochNumber]`) in `defaultPendingClaimBasis`, and make `_claimDefaultedInstantWithdrawRequest` haircut all epochs' receipts, not just `defaultEpoch`.

### Proof of Concept
Foundry fork outline (mirroring `testFinalizeDefaultClaimsFundedAndDefaultedInstantRedeemsInOneCall` in `test/foundry/IdleCreditVault.t.sol:4485`):

1. Attacker deposits AA, calls `requestInstantWithdraw` in epoch N with the CDO under-liquid so `pendingInstantWithdraws` remains > 0 after `getInstantWithdrawFunds` (partial collect via `collectInstantWithdrawFunds`).
2. Epoch N stops cleanly; `epochNumber` becomes N+1. Attacker's `instantWithdrawsRequests[user]` still holds an unfunded remainder recorded under `instantWithdrawsRequestsByEpoch[user][N]`.
3. Borrower defaults in epoch N+1; owner calls `finalizeDefault(recovered, source)`. `defaultPendingClaimBasis()` omits the epoch-N amount; `defaultRecoveryPrice` is inflated.
4. Attacker calls `claimInstantWithdrawRequest`: the epoch-N remainder survives `_claimDefaultedInstantWithdrawRequest` (which only clears `defaultEpoch`) and is paid at par by `_transferFundedClaim`.
5. Assert: attacker received more than `basis * defaultRecoveryPrice`, and a second defaulted-epoch claimant's payout is correspondingly shorted / the reserve is drained below accounted obligations.

Caveat: I was unable to execute the PoC or fully read `_transferFundedClaim`'s reserve guard (the indexed content ended mid-function at line ~900). If that guard strictly isolates `defaultRecoveryReserve` and the old receipt is paid only from genuinely funded strategy cash, the impact degrades to paying an unbacked receipt from other claimants' funded balance — still an overpayment/insolvency bug, but the exact fund source should be confirmed by running the test above.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L366-374)
```text
    instantWithdrawsRequests[_user] += _amount;
    uint256 currentEpoch = epochNumber;
    // we record both per-user (old, kept for compatibility) and per-epoch so on
    // finalization we can distinguish "default-epoch pending instant receipts"
    // from old funded instant receipts.
    instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
    // increase the total instant withdraw requests
    pendingInstantWithdraws += _amount;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L601-616)
```text
    _onlyIdleCDO();
    if (_amount > 0) {
      underlyingToken.safeTransferFrom(msg.sender, address(this), _amount);
      _mint(msg.sender, _amount);
    }

    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
    } else {
      // deposit done between epochs so we increase the counter
      totEpochDeposits += _amount;
    }

    return _amount;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-649)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L679-699)
```text
    uint256 pendingBasis = defaultPendingClaimBasis();
    uint256 totalBasis = activeBasis + pendingBasis;
    if (totalBasis == 0) revert NotAllowed();

    // Some recovery funds may already be in this strategy: partially prefunded instant requests
    // and borrower-send funds that failed at epoch start. Count both without pulling them again.
    uint256 prefundedReserve = _defaultPrefundedInstantReserve();
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
    defaultRecoveryEpoch = epochNumber;
    // A non-zero pending instant bucket means current-epoch instant receipts were not fully funded
    // and must be paid through the same recovery ratio as normal pending receipts.
    defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0;
    // Bring active CDO NAV to the same recovery ratio. IdleCDOEpochVariant then calls
    // _forceUpdateAccounting so tranche prices/virtualPrice expose the crystallized loss.
    uint256 activeFinalNAV = (activeBasis * recoveryPrice) / RECOVERY_FULL;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L716-723)
```text
  function _defaultPrefundedInstantReserve() internal view returns (uint256 prefundedReserve) {
    uint256 pendingInstant = pendingInstantWithdraws;
    if (pendingInstant == 0) return prefundedReserve;
    uint256 instantBasis = instantWithdrawClaimsByEpoch[epochNumber];
    if (instantBasis > pendingInstant) {
      prefundedReserve = instantBasis - pendingInstant;
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L842-856)
```text
  function _claimDefaultedInstantWithdrawRequest(address _user) internal returns (uint256 claimBasis) {
    uint256 defaultEpoch = defaultRecoveryEpoch;
    claimBasis = instantWithdrawsRequestsByEpoch[_user][defaultEpoch];
    if (claimBasis == 0) return claimBasis;

    instantWithdrawsRequestsByEpoch[_user][defaultEpoch] = 0;
    instantWithdrawsRequests[_user] -= claimBasis;
    uint256 pending = pendingInstantWithdraws;
    // `pendingInstantWithdraws` is only the unfunded remainder. If this user's claim is larger,
    // the extra amount was already counted as prefunded reserve during default finalization.
    pendingInstantWithdraws = claimBasis >= pending ? 0 : pending - claimBasis;
    instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis;
    _burn(_user, claimBasis);
    _transferDefaultRecovery(_user, (claimBasis * defaultRecoveryPrice) / RECOVERY_FULL);
  }
```
