### Title
Loss-adjusted stopEpoch haircut bypasses pending instant-withdraw receipts, letting pre-loss instant claims pay out at par - ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
`collectWithdrawFunds` / `previewLossAdjustedWithdrawFunds` only haircut the normal `pendingWithdraws` bucket via `lossRecoveryPriceByEpoch`. Pending instant-withdraw receipts (`instantWithdrawsRequests`, `instantWithdrawsRequestsByEpoch`, `pendingInstantWithdraws`) are excluded from the loss split, and `claimInstantWithdrawRequest` pays them 1:1 with no check against the loss epoch. An unprivileged user who holds an unfunded instant receipt when `stopEpochWithDuration` crystallizes a loss escapes the haircut entirely and drains funded underlying that belongs to loss-adjusted claimants.

### Finding Description
The bug class (missing bounds/scope check on indexed data) maps to a missing coverage check on which receipts a realized loss applies to:

- `previewLossAdjustedWithdrawFunds` computes `pendingBasis = pendingWithdraws` only; `pendingInstantWithdraws` never enters the split, so the borrower is asked to fund less (or the loss is pushed entirely onto active LPs) while instant receipts remain whole. [1](#0-0) 
- `collectWithdrawFunds` stores `lossRecoveryPriceByEpoch[epochNumber]` keyed to the epoch's *normal* receipts and clears `pendingWithdraws`, but never touches `pendingInstantWithdraws` or `instantWithdrawsRequestsByEpoch`. [2](#0-1) 
- `claimInstantWithdrawRequest` burns the full `instantWithdrawsRequests[_user]` and pays it at par via `_transferFundedClaim`; the only haircut path (`_claimDefaultedInstantWithdrawRequest`) is gated on `defaultRecoveryFinalized && defaultInstantWithdrawsFinalized`, which is false for a `stopEpochWithDuration` loss. [3](#0-2) 
- `requestInstantWithdraw` (unlike `requestWithdraw`, which reverts at lines 263-271 when an unclaimed loss-adjusted receipt exists) performs no loss-epoch check, so an attacker can even hold an instant receipt across a loss epoch with no friction. [4](#0-3) 

This happens in practice whenever `startEpoch`/`getInstantWithdrawFunds` under-funds the instant queue (`pendingInstantWithdraws != 0`) and the subsequent `stopEpochWithDuration(_lossAmount)` applies a loss: `defaultPendingClaimBasis` acknowledges that a non-zero `pendingInstantWithdraws` means unfunded receipts exist, yet the loss path ignores them. [5](#0-4) 

### Impact Explanation
Direct theft / unfair payout: suppose `pendingWithdraws = 900`, `pendingInstantWithdraws = 100`, and the borrower funds only `pendingBasis` minus a 50% loss. Normal requesters claim at `lossRecoveryPrice = 0.5` (450 total), while an instant requester claims the full 100 at par from the same collected funds. The instant claimant's extra ~50 is paid out of underlying that the waterfall intended to fund the haircutted queue, leaving late normal claimants unable to withdraw their full `claimBasis * lossRecoveryPrice` — a solvency/fair-burn invariant break with loss bounded only by `pendingInstantWithdraws`.

### Likelihood Explanation
Requires (a) an unprivileged instant-withdraw request left unfunded (achievable whenever epoch liquidity doesn't cover the instant queue — no privileged collusion needed beyond the honest manager running epochs), and (b) a subsequent epoch stopped with a loss via `stopEpochWithDuration`. Attacker is a standard KYC'd lender requesting an instant withdraw; no privileged role needed. The missing check is unconditional in code — the only mitigating factor is that `pendingInstantWithdraws` must be non-zero at loss time.

### Recommendation
Include `pendingInstantWithdraws` in the pending basis for `previewLossAdjustedWithdrawFunds` and `collectWithdrawFunds`, record per-epoch instant receipt basis (the `instantWithdrawClaimsByEpoch` mapping already exists) and apply the same `lossRecoveryPriceByEpoch` haircut inside `claimInstantWithdrawRequest` for the loss epoch, mirroring `_claimDefaultedInstantWithdrawRequest`. Alternatively, revert `collectWithdrawFunds` when `pendingInstantWithdraws != 0` and a loss is applied so instant receipts must be funded before the loss epoch closes.

### Proof of Concept
Foundry fork PoC sketch (repo has `test/foundry/IdleCreditVault.t.sol` harness to reuse):

1. Deploy/attach `IdleCDOEpochVariant` + `IdleCreditVault`; KYC two lenders `A` (normal) and `B` (instant).
2. `A` deposits and calls `requestWithdraw(900)`; `B` deposits and calls `requestInstantWithdraw(100)`.
3. Let the epoch run with insufficient instant funding so `collectInstantWithdrawFunds` covers only part (e.g., 0) of B's request, leaving `pendingInstantWithdraws == 100`.
4. Manager calls `stopEpochWithDuration` with `_lossAmount = 500` → `collectWithdrawFunds(450)`, `lossRecoveryPriceByEpoch[epoch] = 0.5e18`.
5. `B` calls `claimInstantWithdrawRequest` → receives **100** at par (assert succeeds).
6. `A` calls `claimWithdrawRequest` → expects `900 * 0.5 = 450`, but strategy holds only `450 - 100 = 350` → final claim underflows/reverts or pays less than the promised haircutted amount, demonstrating B's haircut escape directly steals from funded normal claims.

### Citations

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L440-460)
```text
  function previewLossAdjustedWithdrawFunds(uint256 _lossAmount) external view returns (uint256 pendingToFund, uint256 activeLoss) {
    uint256 pendingBasis = pendingWithdraws;
    // Full zero-loss funding is safe for legacy aggregate receipts and needs no migration call.
    if (_lossAmount == 0) return (pendingBasis, _lossAmount);

    IIdleCDOEpochVariant cdo = IIdleCDOEpochVariant(idleCDO);
    uint256 activeBasis = _lossActiveBasis(cdo);
    if (pendingBasis == 0) {
      if (_lossAmount > activeBasis) revert NotAllowed();
      return (0, _lossAmount);
    }

    // Legacy pending receipts do not have the per-epoch ownership data needed to store a haircut.
    if (!defaultRecoveryInitialized) revert NotAllowed();
    uint256 totalBasis = activeBasis + pendingBasis;
    if (_lossAmount >= totalBasis) revert NotAllowed();

    uint256 pendingLoss = _lossAmount * pendingBasis / totalBasis;
    pendingToFund = pendingBasis - pendingLoss;
    activeLoss = _lossAmount - pendingLoss;
  }
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
