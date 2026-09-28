### Title
Missing zero-value validation in `createWriteOffRequest` lets any fulfiller seize escrowed tranche tokens for free - ([File: contracts/IdleCreditVaultWriteOffEscrow.sol](contracts/IdleCreditVaultWriteOffEscrow.sol))

### Summary
`IdleCreditVaultWriteOffEscrow.createWriteOffRequest` validates that the deposited tranche amount is non-zero (`if (amount == 0) revert NotAllowed()`), but performs no equivalent validation on `underlyingsRequested`. Like the incomplete `MaxPoolGrad` fix (validation added for one tensor dimension but not its counterpart), one side of the exchange is validated while the other is not. A lender who creates or tops up a request with `underlyingsRequested == 0` produces an escrow entry that `fullfillWriteOffRequest` accepts with `_underlyings = 0`, since `0 < 0` is false. Any EOA can then withdraw the lender's escrowed tranche tokens without paying anything.

### Finding Description
- `createWriteOffRequest` reverts on `amount == 0` but not on `underlyingsRequested == 0` [1](#0-0) 
- `fullfillWriteOffRequest` only checks `currentRequest.tranches != _tranches || _underlyings < currentRequest.underlyings`. When `currentRequest.underlyings == 0`, a fulfiller can pass the full `_tranches` amount and `_underlyings = 0`, satisfying the check [2](#0-1) 
- The fulfiller then receives `IERC20Detailed(tranche).safeTransfer(msg.sender, _tranches)` while paying `_underlyings - _totFee = 0` underlying [3](#0-2) 
- Because `userRequests` accumulates (`currentRequest.underlyings + underlyingsRequested`), a request accidentally created with 0 cannot be "fixed" except by `deleteWriteOffRequest`, leaving a race window.

### Impact Explanation
Direct theft. The escrowed tranche tokens represent a claim on vault NAV redeemable via `IdleCDOEpochVariant.requestWithdraw`/`claimWithdrawRequest`. An unprivileged attacker (any EOA — `fullfillWriteOffRequest` is permissionless, "this function can be called by any wallet") drains them for zero underlying. Loss equals the full tranche-token value of the mispriced request. No privileged role, default, or frozen state is required — the attack executes during a normal running epoch (the only phase `createWriteOffRequest` allows).

### Likelihood Explanation
Medium-low: it requires a lender to create a request with `underlyingsRequested == 0` (e.g., a UI bug, a partial top-up flow, or intentionally signaling "make me an offer"). However, the accumulation design makes this plausible: a lender may call `createWriteOffRequest(amount, 0)` intending to set price later — but the API offers no way to raise `underlyings` other than depositing more tranches. Once the zero-price request exists, fulfillment is atomic, permissionless, and front-runnable, so theft of the escrowed tranches is guaranteed before the lender can `deleteWriteOffRequest`.

### Recommendation
Revert in `createWriteOffRequest` when `underlyingsRequested == 0`, symmetric with the `amount == 0` check:

```solidity
if (amount == 0 || underlyingsRequested == 0) revert NotAllowed();
```

Optionally also enforce `_underlyings == currentRequest.underlyings` in `fullfillWriteOffRequest` if overpayment is not a desired feature.

### Proof of Concept
Foundry fork PoC sketch:

```solidity
// epoch running; victim is KYC-allowed lender holding AA tranches
vm.startPrank(victim);
tranche.approve(address(escrow), 100e18);
// victim mistakenly/skeletally creates request with 0 underlyings
escrow.createWriteOffRequest(100e18, 0);
vm.stopPrank();

// attacker (any EOA) fulfills paying nothing
uint256 pre = tranche.balanceOf(attacker);
vm.prank(attacker);
escrow.fullfillWriteOffRequest(victim, 100e18, 0); // does not revert
assertEq(tranche.balanceOf(attacker) - pre, 100e18); // stole the tranches for free
assertEq(underlying.balanceOf(victim), victimPre);   // victim received 0
```

Guards that do not stop it: `nonReentrant` (single tx), `EpochNotRunning` (satisfied in normal operation), `WrongRequest` (passes trivially), `pendingUnderlyings` accounting (updated correctly with 0).

### Citations

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L86-101)
```text
  function createWriteOffRequest(uint256 amount, uint256 underlyingsRequested) external nonReentrant {
    // can request write off only when epoch is running
    if (!IdleCDOEpochVariant(idleCDOEpoch).isEpochRunning()) revert EpochNotRunning();
    // cannot request write off with 0 tranche tokens
    if (amount == 0) revert NotAllowed();

    // get tranche tokens from user
    IERC20Detailed(tranche).safeTransferFrom(msg.sender, address(this), amount);
    // get current write-off request
    WriteOffRequest memory currentRequest = userRequests[msg.sender];
    // update user requests
    userRequests[msg.sender] = WriteOffRequest({
      tranches: currentRequest.tranches + amount,
      underlyings: currentRequest.underlyings + underlyingsRequested
    });
    pendingUnderlyings += underlyingsRequested;
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L123-135)
```text
  function fullfillWriteOffRequest(address _user, uint256 _tranches, uint256 _underlyings) external nonReentrant {
    // get current write-off request
    WriteOffRequest memory currentRequest = userRequests[_user];
    // check if the user has a write-off request
    if (currentRequest.tranches == 0) revert Is0();
    // check if the request matches at least the expected values (borrower can choose to overpay if needed, but not underpay)
    if (currentRequest.tranches != _tranches || _underlyings < currentRequest.underlyings) {
      revert WrongRequest();
    }

    // Existing upgraded escrows can have legacy requests that were never added to pendingUnderlyings.
    pendingUnderlyings -= pendingUnderlyings >= currentRequest.underlyings ? currentRequest.underlyings : pendingUnderlyings;
    delete userRequests[_user];
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L139-151)
```text
    underlyingToken.safeTransferFrom(msg.sender, address(this), _underlyings);
    // check if the exit fee is set and if so, apply it
    uint256 _exitFee = exitFee;
    uint256 _totFee;
    if (_exitFee > 0) {
      _totFee = (_underlyings * _exitFee) / FULL_VALUE;
      // transfer exit fee to the feeReceiver
      underlyingToken.safeTransfer(feeReceiver, _totFee);
    }
    // transfer the remaining underlyings to the user
    underlyingToken.safeTransfer(_user, _underlyings - _totFee);
    // transfer tranche tokens to fulfiller
    IERC20Detailed(tranche).safeTransfer(msg.sender, _tranches);
```
