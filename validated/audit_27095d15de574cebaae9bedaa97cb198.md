### Title
Stale write-off requests can be force-fulfilled by any third party at user-set terms without re-confirmation, letting a buyer capture accrued tranche appreciation - ([File: contracts/IdleCreditVaultWriteOffEscrow.sol](contracts/IdleCreditVaultWriteOffEscrow.sol))

### Summary
The external bug class is "an action that permanently binds/links a user's state is executed without an explicit confirmation step at execution time." The analog in idle-tranches is `IdleCreditVaultWriteOffEscrow.fullfillWriteOffRequest`: a lender's escrowed tranche position is permanently converted to underlying at the fixed price recorded in a stale `WriteOffRequest`, executable by any wallet, with no re-confirmation, expiry, or oracle check at fulfillment time.

### Finding Description
A lender creates a write-off request via `createWriteOffRequest(amount, underlyingsRequested)`, which escrows their tranche tokens and stores a fixed `underlyings` ask [1](#0-0) . Fulfillment is permissionless — the contract comments explicitly state "this function can be called by any wallet" and the only checks are that the request exists and `_underlyings >= currentRequest.underlyings` [2](#0-1) . The test `testFullfillWriteOffRequestAllowsThirdPartyBuyer` confirms any arbitrary `buyer` can fulfill [3](#0-2) .

The user's consent is a one-time signature at request creation — equivalent to the OAuth callback that auto-links the account without a Confirm step. Between creation and fulfillment, tranche value evolves: the epoch runs, interest accrues, and `tranchePrice` rises (or the lender renegotiates off-chain and the stored terms become stale). There is no deadline/`validUntil`, no price re-check, and no user confirmation hook at fulfillment.

The lender's only escape is `deleteWriteOffRequest` [4](#0-3) , but that requires an active transaction that a buyer can lose the race to — an attacker monitoring the mempool can front-run the cancellation with `fullfillWriteOffRequest` in the same or earlier block, since fulfillment only needs `approve` + one call.

### Impact Explanation
Direct theft of accrued value. Suppose an AA holder escrows 10,000 tranche tokens requesting 10,000 USDC when `tranchePrice(AA) ≈ 1.0`. After interest accrual pushes the price to 1.05, the position is worth ~10,500 USDC at redemption, but any EOA can fulfill at the stale 10,000 USDC ask and immediately hold tranche tokens redeemable for ~10,500 USDC at epoch end — a ~500 USDC transfer from the lender to the buyer, scaling linearly with position size and elapsed interest. The asymmetric option is free for buyers: they fulfill only when the stale price favors them, and simply ignore requests whose terms became unfavorable (e.g., after a loss event where `stopEpochWithDuration(_lossAmount)` drops tranche prices below the ask).

### Likelihood Explanation
Requires only an unprivileged attacker with USDC and a stale request whose fixed ask is below current tranche value. Write-off requests can persist across entire epochs; every day the epoch runs, accrued yield widens the gap. No privileged-role involvement, no oracle manipulation, no timing beyond a single mempool-observable front-run of `deleteWriteOffRequest` if the lender tries to cancel. The design choice to allow third-party fulfillers (rather than only `borrower`) maximizes the attacker set.

### Recommendation
- Bind fulfillment to a fresh consent step: require the seller to countersign/approve fulfillment (e.g., an `acceptFulfillment` call or EIP-712 signature valid for a short window), mirroring the Socialstream fix's explicit "Confirm" route.
- Alternatively, add `deadline`/`maxTranchePriceSlippage` fields to `WriteOffRequest` and enforce `block.timestamp <= deadline` at fulfillment.
- At minimum, restrict `fullfillWriteOffRequest` to `borrower` so stale terms can only be exercised by the counterparty the lender negotiated with off-chain.

### Proof of Concept
```solidity
// test/foundry/IdleCreditVaultWriteOffEscrowStale.t.sol — fork mainnet, reuse escrow setup
function testStaleRequestFulfilledByArbitraryBuyer() external {
    uint256 requestedTranches = 10_000e18;
    uint256 requestedUnderlyings = 10_000e6; // ~price 1.0 at request time

    // 1) LP escrows tranche tokens at a fixed ask while epoch is running
    vm.prank(LP);
    escrow.createWriteOffRequest(requestedTranches, requestedUnderlyings);

    // 2) Epoch runs; tranche price appreciates (interest accrual)
    vm.warp(block.timestamp + 30 days);
    // (optionally trigger price update via the strategy's price mechanics used in the test harness)

    uint256 priceNow = cdoEpoch.tranchePrice(address(tranche));
    assertGt(priceNow * requestedTranches / 1e18, requestedUnderlyings); // stale ask < market

    // 3) LP realizes terms are stale and tries to cancel; attacker front-runs
    address attacker = makeAddr("attacker");
    deal(address(underlying), attacker, requestedUnderlyings);
    vm.startPrank(attacker);
    underlying.approve(address(escrow), requestedUnderlyings);
    escrow.fullfillWriteOffRequest(LP, requestedTranches, requestedUnderlyings); // succeeds, no borrower-only check
    vm.stopPrank();

    // LP's delete now reverts — tokens already taken
    vm.prank(LP);
    vm.expectRevert(abi.encodeWithSelector(Is0.selector));
    escrow.deleteWriteOffRequest();

    // 4) Attacker's acquired tranches are worth more than paid at epoch-stop redemption
    assertEq(tranche.balanceOf(attacker), requestedTranches);
}
```

### Citations

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L86-102)
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
  }
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L105-115)
```text
  function deleteWriteOffRequest() external nonReentrant {
    // get current write-off request
    WriteOffRequest memory currentRequest = userRequests[msg.sender];
    // check if the user has a write-off request
    if (currentRequest.tranches == 0) revert Is0();

    // Existing upgraded escrows can have legacy requests that were never added to pendingUnderlyings.
    pendingUnderlyings -= pendingUnderlyings >= currentRequest.underlyings ? currentRequest.underlyings : pendingUnderlyings;
    delete userRequests[msg.sender];
    // transfer tranche tokens back to the user
    IERC20Detailed(tranche).safeTransfer(msg.sender, currentRequest.tranches);
```

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L122-131)
```text
  /// @dev this function can be called by any wallet
  function fullfillWriteOffRequest(address _user, uint256 _tranches, uint256 _underlyings) external nonReentrant {
    // get current write-off request
    WriteOffRequest memory currentRequest = userRequests[_user];
    // check if the user has a write-off request
    if (currentRequest.tranches == 0) revert Is0();
    // check if the request matches at least the expected values (borrower can choose to overpay if needed, but not underpay)
    if (currentRequest.tranches != _tranches || _underlyings < currentRequest.underlyings) {
      revert WrongRequest();
    }
```

**File:** test/foundry/IdleCreditVaultWriteOffEscrow.t.sol (L226-231)
```text
    vm.startPrank(buyer);
    underlying.approve(address(escrow), requestedUnderlyings);
    escrow.fullfillWriteOffRequest(LP, requestedTranches, requestedUnderlyings);
    vm.expectRevert(abi.encodeWithSelector(NotAllowed.selector));
    cdoEpoch.writeOffDeposit(requestedTranches, address(tranche));
    vm.stopPrank();
```
