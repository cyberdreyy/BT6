### Title
Stale-epoch instant withdraw receipts bypass default recovery haircut — index keyed to current `epochNumber` misses receipts recorded in earlier epochs - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary

The kernel bug is an index (`addr_to_vb_xa` hash) that assumes `cpu_possible_mask` is contiguous, dereferencing a CPU slot that was never set up. `IdleCreditVault` has the same structural flaw: instant-withdraw receipt data is keyed by the *request* epoch (`instantWithdrawsRequestsByEpoch[user][epoch]`, `instantWithdrawClaimsByEpoch[epoch]`), but default finalization and claiming both index these maps at the *current/default* `epochNumber`, silently assuming every still-pending instant receipt was created in the epoch that defaulted. When a partially funded instant request survives across epochs (a "gap" in the epoch index space), its basis is invisible at finalization, inflating the recovery price, and the claimant falls through to the funded-claim path that pays the receipt at par (or reverts and permanently freezes it).

### Finding Description

`requestInstantWithdraw` records receipts at the *current* epoch at the time of the request:

```solidity
instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
``` [1](#0-0) 

`collectInstantWithdrawFunds` lets the CDO fund only part of the instant queue, so `pendingInstantWithdraws` can remain non-zero while the receipts backing it belong to an older epoch [2](#0-1) . `epochNumber` is only bumped inside `deposit` while the epoch is running [3](#0-2) , so epochs advance while the receipts stay indexed under their original epoch — exactly the "sparse index space" the kernel fix addresses.

At default finalization, `defaultPendingClaimBasis` looks up instant claims only at `epochNumber` (the default epoch):

```solidity
basis = pendingWithdraws;
if (pendingInstantWithdraws != 0) {
  basis += instantWithdrawClaimsByEpoch[epochNumber];
}
``` [4](#0-3) 

For a stale-epoch receipt this adds `0`, so `totalBasis` is understated and `recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis` is inflated [5](#0-4) . `_defaultPrefundedInstantReserve` has the same wrong-epoch lookup, so the already-funded portion of the stale receipt is also omitted from the reserve [6](#0-5) .

On claim, `defaultInstantWithdrawsFinalized` is true (`pendingInstantWithdraws != 0`), so `_claimDefaultedInstantWithdrawRequest` runs first — but it also reads only `instantWithdrawsRequestsByEpoch[_user][defaultRecoveryEpoch]`, which is `0` for the stale receipt, and returns without clearing anything [7](#0-6) . Execution then falls through to the *funded* path in `claimInstantWithdrawRequest`, which burns the full receipt and calls `_transferFundedClaim` for the full `instantWithdrawsRequests[_user]` — i.e., payment at 100% with no recovery haircut, drawn from strategy balance that must merely clear the `defaultRecoveryReserve` guard [8](#0-7) . Since the stale receipt's basis was never included in `totalBasis`, the reserve was sized without it: the inflated `defaultRecoveryPrice` over-promises to defaulted-epoch claimants while the stale-epoch claimant withdraws at par from non-reserve balance.

### Impact Explanation

Direct theft / insolvency. Concretely, in a vault where an instant request of amount `A` was made in epoch `N` and only partially funded, then the borrower defaults in epoch `M > N`:

- `defaultPendingClaimBasis()` misses `A` (it reads `instantWithdrawClaimsByEpoch[M] == 0`), so `recoveryPrice` is computed against a `totalBasis` that is `A` too small — every defaulted-epoch claimant and post-default requester is overpaid relative to fair socialization.
- The stale-epoch claimant calls `claimInstantWithdrawRequest` (via the CDO's claim path) and receives the full `A` at par through `_transferFundedClaim`, consuming underlying that was economically meant to be haircut — a direct transfer of value from the recovery pool to one claimant.
- If the par payment would leave `balance - defaultRecoveryReserve < A`, the claim instead reverts in `_transferFundedClaim`, permanently freezing the user's receipt with no recovery path, because `_claimDefaultedInstantWithdrawRequest` can never match their epoch key.

Either branch breaks the "one receipt one payout at the recovery ratio" invariant. The loss is bounded by the stale instant receipts outstanding (`instantWithdrawsRequests[user]` at default), which can be the entire unfunded instant queue.

### Likelihood Explanation

Requirements: (1) an unprivileged user makes an instant withdraw request in epoch `N`; (2) the CDO collects only part of the instant funds that epoch (`collectInstantWithdrawFunds` partial funding is a supported, honest-manager path — `pendingInstantWithdraws` remains > 0); (3) at least one more epoch boundary passes so `epochNumber` advances past `N` while the receipt stays pending; (4) the borrower defaults and `finalizeDefaultRecovery` runs. None of these steps require the attacker to hold any privileged role; steps 2–4 are normal protocol operation driven by honest manager/borrower actions. The attacker only needs to be an instant-withdraw requester (any KYC-passing tranche holder) whose request survives an epoch boundary unfunded, then claim after finalization — or simply be frozen out. No existing guard stops it: the `_hasWithdrawRequest`/post-default guards apply only to *new* requests after finalization, and the reserve guard in `_transferFundedClaim` protects the reserve, not the par overpayment drawn outside it.

### Recommendation

Track the epoch(s) carrying unfunded instant receipts instead of assuming the current epoch:

- In `defaultPendingClaimBasis` and `_defaultPrefundedInstantReserve`, aggregate `instantWithdrawClaimsByEpoch` over all epochs that still contribute to `pendingInstantWithdraws` — e.g., maintain a separate `pendingInstantBasis` counter incremented in `requestInstantWithdraw` and decremented as instant receipts are funded/cleared, so no per-epoch lookup is needed at finalization.
- In `_claimDefaultedInstantWithdrawRequest`, clear the user's *actual* outstanding basis (e.g., iterate the recorded request epochs, or store the user's open request-epoch list / track `instantWithdrawsRequests[user]` as the claimable basis and haircut it directly) rather than looking up only `defaultRecoveryEpoch`.
- Analogously to the kernel fix ("adjust index to the next possible CPU"), the finalization code must resolve the epoch key that actually holds the pending basis instead of assuming contiguity.

### Proof of Concept

Foundry fork PoC sketch (against `test/foundry` harness using `IdleCreditVault` + `IdleCDOEpochVariant`):

```solidity
function testStaleEpochInstantReceiptSkipsHaircut() external {
  // Epoch N running. user (KYC'd tranche holder) requests instant withdraw of A.
  _depositWithUser(user, 100e6);
  uint256 t = tranche.balanceOf(user);
  vm.prank(user);
  cdoEpoch.requestInstantWithdraw(t); // -> strategy.requestInstantWithdraw at epoch N

  // Honest manager funds only part of instant queue this epoch.
  // (CDO path that calls strategy.collectInstantWithdrawFunds(partial))
  // pendingInstantWithdraws = A - funded > 0, receipt stays keyed at epoch N.

  // Epoch advances: stopEpoch + startEpoch => strategy.deposit() bumps epochNumber to N+1..M.
  _stopCurrentEpoch(); _startEpoch();

  // Borrower defaults in epoch M; manager/owner finalize recovery.
  // finalizeDefaultRecovery: totalBasis excludes A (instantWithdrawClaimsByEpoch[M]==0)
  //   => defaultRecoveryPrice inflated; defaultInstantWithdrawsFinalized = true.
  _finalizeDefaultWithPartialRecovery();

  // user claims: _claimDefaultedInstantWithdrawRequest hits epoch M -> 0, returns;
  // funded path pays full instantWithdrawsRequests[user] at par (or reverts if
  // balance - reserve < amount, freezing the claim).
  vm.prank(user);
  cdoEpoch.claimInstantWithdrawRequest(); // receives A * 1.0, not A * defaultRecoveryPrice
}
```

Assertion: user's payout equals `A` (par) while `defaultRecoveryPrice < 1e18`, and `defaultRecoveryReserve` was never charged for that payment — demonstrating both the inflated recovery price and the unhaircutted par payout caused by the wrong-epoch index lookup.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L367-372)
```text
    uint256 currentEpoch = epochNumber;
    // we record both per-user (old, kept for compatibility) and per-epoch so on
    // finalization we can distinguish "default-epoch pending instant receipts"
    // from old funded instant receipts.
    instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L382-392)
```text
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L607-610)
```text
    if (IIdleCDOEpochVariant(idleCDO).isEpochRunning()) {
      // deposit done on stopEpoch (before setting the var to false) so we reset the counter
      totEpochDeposits = 0;
      epochNumber += 1;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-648)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L679-692)
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
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L716-722)
```text
  function _defaultPrefundedInstantReserve() internal view returns (uint256 prefundedReserve) {
    uint256 pendingInstant = pendingInstantWithdraws;
    if (pendingInstant == 0) return prefundedReserve;
    uint256 instantBasis = instantWithdrawClaimsByEpoch[epochNumber];
    if (instantBasis > pendingInstant) {
      prefundedReserve = instantBasis - pendingInstant;
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L842-855)
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
```
