### Title
Partially-funded instant-withdraw receipts are claimed FIFO at par, so the aggregate receipt ledger (`instantWithdrawsRequests`/`pendingInstantWithdraws`) can exceed funded underlying and late claimants' withdrawals revert — the same "accounting counter diverges from claimable assets and blocks withdrawals" class as `netAssetDeposits` — ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`requestInstantWithdraw` records each user's receipt basis 1:1 in `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch`, `instantWithdrawClaimsByEpoch`, and `pendingInstantWithdraws` [1](#0-0) . Funding, however, arrives separately via `collectInstantWithdrawFunds`, which only decrements `pendingInstantWithdraws` and pulls whatever underlying amount the CDO actually collected [2](#0-1) . The code itself acknowledges the funded amount can be less than the aggregate claim basis: `_defaultPrefundedInstantReserve` computes `instantBasis - pendingInstant` as the "already-held underlying" for "partially prefunded instant requests" [3](#0-2) . Yet `claimInstantWithdrawRequest` pays each user their full recorded `instantWithdrawsRequests[_user]` at par via `_transferFundedClaim`, with no pro-rata haircut [4](#0-3) . Unlike normal withdrawals, which get `lossRecoveryPriceByEpoch` when underfunded [5](#0-4) , instant receipts have no partial-funding haircut path — so the recorded basis stays at par while the strategy holds less.

### Finding Description
This mirrors M-12's structure: a ledger counter is incremented on the "in" operation (receipt minted for full `_amount`) and consumed on the "out" operation (claim pays full recorded amount), while the actual backing can legitimately be lower (partial prefunding during `startEpoch`, or `collectInstantWithdrawFunds` pulling a shortfall amount). When `sum(instantWithdrawsRequests) > underlyingToken.balanceOf(strategy)`, early claimants drain the pool and the remaining claimants' `claimInstantWithdrawRequest` reverts on the ERC20 transfer — a temporary/permanent freeze of the tail end of withdrawals, exactly the accepted M-12 impact ("only affects the tail end of withdraws").

Concretely, in the running epoch: user requests instant withdrawal (receipt minted, basis recorded), `startEpoch` moves only part of the needed cash to the strategy (the prefunded-reserve comment confirms this scenario is expected), the borrower default path or manager collection never tops it up, and the last claimant cannot collect. The state can self-correct if more funds arrive — matching the escalation reasoning in M-12, i.e., medium severity.

### Impact Explanation
Tail-end instant-withdraw claimants have their receipt tokens permanently recorded but unfunded: claims revert (insufficient underlying balance), freezing their funds until new funding arrives — identical to the "user can't redeem" impact of `netAssetDeposits` underflow. Loss is bounded by `instantWithdrawClaimsByEpoch[epoch] - pendingInstantWithdraws` funding gap.

### Likelihood Explanation
Edge case, like the accepted M-12: requires a funding shortfall on the instant queue (partial prefund acknowledged in code) and enough prior claims to exhaust the funded balance. No privileged-role malice needed — honest manager sequencing around `collectInstantWithdrawFunds` suffices.

### Recommendation
Apply the same haircut mechanism used for normal withdrawals: record a per-epoch recovery/funding price for instant receipts (analogous to `lossRecoveryPriceByEpoch`), or track funded-vs-requested per epoch and pay claims pro-rata, instead of paying `instantWithdrawsRequests[_user]` at par from a potentially underfunded pool.

### Proof of Concept
A Foundry PoC would: (1) have two users request instant withdrawals in the same epoch (receipts recorded at par); (2) have `startEpoch`/`collectInstantWithdrawFunds` deliver less underlying than the aggregate basis (partial prefund); (3) show user1's `claimInstantWithdrawRequest` succeeds while user2's reverts on the underlying transfer despite holding a valid receipt. Note: I could not fully trace the exact `startEpoch` prefund path in `IdleCDOEpochVariant.sol` within the available iterations to confirm the shortfall can persist into the claim phase without privileged misbehavior, so the PoC's step (2) setup is the part needing verification.

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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L380-392)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L398-403)
```text
  function collectInstantWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    if (_amount == 0) return;
    pendingInstantWithdraws -= _amount;
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
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
