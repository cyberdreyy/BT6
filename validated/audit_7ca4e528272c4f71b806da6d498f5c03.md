### Title
Claimed instant-withdraw receipts remain claimable after borrower default, enabling double payout - ([contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`claimInstantWithdrawRequest` clears only the aggregate `instantWithdrawsRequests` balance. It does not clear `instantWithdrawsRequestsByEpoch` or decrement `instantWithdrawClaimsByEpoch`, even though both were populated by `requestInstantWithdraw` [1](#0-0) . If the same epoch later defaults with any unfunded instant-withdraw remainder, finalization enables the defaulted-epoch claim path and the stale per-epoch receipt can be paid a second time from `defaultRecoveryReserve` [2](#0-1) .

### Finding Description
A user creates an instant-withdraw receipt during the buffer phase. `requestInstantWithdraw` mints strategy-token receipts and records the request in `instantWithdrawsRequests`, `instantWithdrawsRequestsByEpoch[user][epochNumber]`, `instantWithdrawClaimsByEpoch[epochNumber]`, and `pendingInstantWithdraws` [3](#0-2) .

When the funded ordinary claim executes, `claimInstantWithdrawRequest` burns the aggregate receipt amount, sets `instantWithdrawsRequests[user]` to zero, and transfers the funded underlyings [4](#0-3) . The per-epoch accounting used by default recovery remains unchanged.

If the borrower then defaults while `pendingInstantWithdraws` is non-zero, `finalizeDefaultRecovery` includes the entire current-epoch instant basis in `defaultPendingClaimBasis` [5](#0-4) . It also sets `defaultInstantWithdrawsFinalized` whenever `pendingInstantWithdraws != 0` [6](#0-5) . A subsequent call to `claimInstantWithdrawRequest` enters `_claimDefaultedInstantWithdrawRequest`, which reads the stale `instantWithdrawsRequestsByEpoch[user][defaultRecoveryEpoch]`, pays `claimBasis * defaultRecoveryPrice`, and only then clears the epoch mapping [7](#0-6) .

The broken invariant is “one withdrawal receipt, one payout.” A receipt already paid at par remains encoded as an unfunded current-epoch default claim.

### Impact Explanation
An unprivileged tranche-token holder can receive its instant withdrawal at par before default finalization and then receive an additional haircutted recovery payment afterward. The second payout is directly withdrawn through `_transferDefaultRecovery`, which decreases `defaultRecoveryReserve` and transfers underlying tokens [8](#0-7) .

For a receipt of `R` and finalized recovery price `p`, the attacker steals `R * p / 1e18` from recovery funds that should be reserved for active holders and genuinely unfunded receipt claimants. If other claimants withdraw first and leave enough reserve, the attacker receives the full second payment; otherwise later legitimate claimants are unable to receive their expected recovery.

### Likelihood Explanation
Likelihood depends on an epoch having partial instant-withdraw funding followed by borrower default in the same epoch. `collectInstantWithdrawFunds` reduces `pendingInstantWithdraws` only by the amount collected, so a partially funded queue leaves `pendingInstantWithdraws != 0` [9](#0-8) . Default finalization then activates the stale-epoch recovery path [10](#0-9) .

No privileged malicious action is required. The owner, manager, and borrower only perform their normal epoch operations; the attacker merely requests and claims an instant withdrawal before the borrower default is finalized.

### Recommendation
In `claimInstantWithdrawRequest`, consume the caller’s per-epoch receipt state together with the aggregate receipt. Specifically:

- determine the amount being claimed before burning;
- subtract it from the appropriate `instantWithdrawsRequestsByEpoch[user][epoch]` entry or entries;
- subtract the same basis from `instantWithdrawClaimsByEpoch[epoch]`;
- preserve aggregate `pendingInstantWithdraws` semantics according to whether the claim was already funded;
- add fork coverage for `requestInstantWithdraw -> funded claim -> default -> finalizeDefaultRecovery -> claimInstantWithdrawRequest`, asserting that the second claim pays zero.

The cleanest fix is to make funded instant claims call a common clearing routine analogous to `_clearWithdrawClaimForEpoch`, rather than clearing only `instantWithdrawsRequests`.

### Proof of Concept
Reproducible Foundry fork sequence:

```solidity
// Assume fixed-APR/non-programmable mode, epoch N is in buffer,
// instant withdrawals enabled, and Alice is a KYC-allowed tranche holder.

// 1. Buffer phase, epochNumber == N.
vm.prank(alice);
cdo.requestWithdraw(aliceTrancheBalance, AATranche);

// If APR drop causes instant mode, IdleCDOEpochVariant calls:
// creditVault.requestInstantWithdraw(R, alice).
uint256 N = creditVault.epochNumber();
assertEq(creditVault.instantWithdrawsRequests(alice), R);
assertEq(creditVault.instantWithdrawsRequestsByEpoch(alice, N), R);

// 2. Manager starts epoch N. Available CDO cash partially prefunds
//    instant withdrawals, leaving pendingInstantWithdraws > 0.
vm.prank(manager);
cdo.startEpoch();

assertGt(creditVault.pendingInstantWithdraws(), 0);

// 3. Manager completes instant funding or the strategy has enough funded
//    underlyings for Alice's normal claim. Alice claims at par.
uint256 aliceBefore = underlying.balanceOf(alice);
vm.prank(alice);
cdo.claimInstantWithdrawRequest();
assertEq(underlying.balanceOf(alice) - aliceBefore, R);

// BUG: only the aggregate request was cleared.
assertEq(creditVault.instantWithdrawsRequests(alice), 0);
assertEq(creditVault.instantWithdrawsRequestsByEpoch(alice, N), R);
assertEq(creditVault.instantWithdrawClaimsByEpoch(N), R);

// 4. Borrower defaults in epoch N while pendingInstantWithdraws > 0.
//    This can happen through the existing getInstantWithdrawFunds catch path
//    or the stopEpoch borrower-funding catch path.
vm.prank(manager);
cdo.getInstantWithdrawFunds(); // borrower transfer fails; defaulted == true

// 5. Owner/manager supplies recovery and finalizes.
uint256 recovery = recoveredAmount;
vm.prank(recoverySource);
underlying.approve(address(creditVault), recovery);
vm.prank(manager);
cdo.finalizeDefaultRecovery(recovery, recoverySource);

assertEq(creditVault.defaultRecoveryEpoch(), N);
assertTrue(creditVault.defaultRecoveryFinalized());
assertTrue(creditVault.defaultInstantWithdrawsFinalized());

// 6. Alice calls the same claim path again and receives a second payout.
aliceBefore = underlying.balanceOf(alice);
vm.prank(alice);
cdo.claimInstantWithdrawRequest();

uint256 expectedSecondPayout =
    R * creditVault.defaultRecoveryPrice() / 1e18;
assertEq(underlying.balanceOf(alice) - aliceBefore, expectedSecondPayout);
```

The assertion demonstrating the issue is that `instantWithdrawsRequestsByEpoch(alice, N)` remains `R` immediately after the funded claim even though `instantWithdrawsRequests(alice)` was already reduced to zero.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L356-374)
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L398-402)
```text
  function collectInstantWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    if (_amount == 0) return;
    pendingInstantWithdraws -= _amount;
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-648)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L690-696)
```text
    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
    defaultRecoveryEpoch = epochNumber;
    // A non-zero pending instant bucket means current-epoch instant receipts were not fully funded
    // and must be paid through the same recovery ratio as normal pending receipts.
    defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0;
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

**File:** contracts/strategies/idle/IdleCreditVault.sol (L912-916)
```text
  function _transferDefaultRecovery(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    // Every defaulted or post-default claim consumes the isolated recovery reserve.
    defaultRecoveryReserve -= _amount;
    underlyingToken.safeTransfer(_user, _amount);
```
