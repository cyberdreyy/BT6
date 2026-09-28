### Title
Default recovery claims can permanently burn payouts by passing the zero recipient - (File: `contracts/DefaultDistributor.sol`)

### Summary
`DefaultDistributor.claim` lets a claimant specify an arbitrary payout recipient and does not reject `address(0)`. After the claimant’s full tranche balance is transferred into the distributor, the underlying recovery payout is sent directly to the supplied recipient. For an underlying token whose `transfer` implementation permits the zero address, such as USDT, the payout becomes permanently inaccessible while the claimant’s tranche position is consumed.

### Finding Description
During default recovery distribution, `claim(address _to)` reads the caller’s entire `trancheToken` balance, pulls it into the distributor, and then transfers the calculated underlying amount to `_to`. There is no `_to != address(0)` validation before `IERC20(token).safeTransfer(_to, ...)`. [1](#0-0) 

The bug is most relevant in the defaulted/finalized phase. Once the honest owner activates the distributor, `rate` is fixed from the distributor’s underlying balance and the tranche-token supply. [2](#0-1)  A tranche holder calling `claim(address(0))` permanently consumes their receipt balance but directs the underlying payout to an address that has no usable private key.

This is distinct from the credit vault’s normal withdrawal path: `claimWithdrawRequest` and `claimInstantWithdrawRequest` derive the receiver from `msg.sender`, so they do not expose an arbitrary recipient parameter. [3](#0-2)  `DefaultDistributor.claim` therefore provides the direct recipient-controlled payout surface analogous to the reported position-transfer recipient bug.

### Impact Explanation
A claimant can permanently lose their entire proportional default-recovery payout. For example, if the distributor holds `200,000 USDT`, total tranche supply is `100`, and a claimant holds `10` tranche tokens, `claim(address(0))` transfers `20,000 USDT` to the zero address and removes the claimant’s tranche balance from circulation.

The lost payout also no longer belongs to the claimant and cannot be recovered through the claim flow. Any residual ability to recover it would depend on the underlying token’s own zero-address handling or an owner rescue operation; `claim` itself provides no correction mechanism.

### Likelihood Explanation
Likelihood is user-error dependent rather than externally profitable. An unprivileged claimant must explicitly pass the zero recipient while claiming their own recovery. The function also gives a recipient parameter for legitimate destination flexibility, increasing the chance that integrations, generated calldata, or callers can supply `address(0)` accidentally.

The issue requires an underlying ERC20 whose zero-address transfer succeeds. USDC commonly rejects transfers to `address(0)`, while USDT does not enforce that invariant, so exploitability depends on the defaulted vault’s configured underlying token.

### Recommendation
Reject the zero recipient before consuming the claimant’s tranche balance:

```solidity
function claim(address _to) external {
  require(isActive, '!ACTIVE');
  require(_to != address(0), 'IS_0');

  IERC20 tranche = IERC20(trancheToken);
  uint256 trancheBal = tranche.balanceOf(msg.sender);
  tranche.safeTransferFrom(msg.sender, address(this), trancheBal);
  IERC20(token).safeTransfer(_to, trancheBal * rate / ONE_TRANCHE);
}
```

A zero-balance check can additionally make the failure explicit before token movement.

### Proof of Concept
The following mainnet-fork test uses USDT because its live ERC20 implementation permits transfers to `address(0)`. It can be added to `test/foundry/DefaultDistributor.t.sol`.

```solidity
function testClaimToZeroAddressBurnsRecoveryPayout() external {
  IERC20Detailed usdt =
    IERC20Detailed(0xdAC17F958D2ee523a2206206994597C13D831ec7);

  address user = address(0x1234);
  uint256 trancheAmount = 10 * 1e18;
  uint256 recoveryAmount = 200_000 * 1e6;

  IdleCDOTranche(address(tranche)).mint(user, trancheAmount);

  distributor = new DefaultDistributor(
    address(usdt),
    address(tranche),
    address(this)
  );

  deal(address(usdt), address(distributor), recoveryAmount);
  distributor.setIsActive(true);

  // rate = 200_000 USDT / 10 tranche tokens
  uint256 expectedPayout = trancheAmount * distributor.rate() / ONE_TRANCHE;
  assertEq(expectedPayout, 200_000 * 1e6);

  vm.startPrank(user);
  tranche.approve(address(distributor), trancheAmount);
  distributor.claim(address(0));
  vm.stopPrank();

  assertEq(tranche.balanceOf(user), 0);
  assertEq(tranche.balanceOf(address(distributor)), trancheAmount);
  assertEq(usdt.balanceOf(address(0)), expectedPayout);
  assertEq(usdt.balanceOf(address(distributor)), 0);
}
```

This demonstrates that the claim accounting completes and the tranche position is consumed even though the recovery payout is delivered to the zero address.

### Citations

**File:** contracts/DefaultDistributor.sol (L35-40)
```text
  function claim(address _to) external {
    require(isActive, '!ACTIVE');
    IERC20 tranche = IERC20(trancheToken);
    uint256 trancheBal = tranche.balanceOf(msg.sender);
    tranche.safeTransferFrom(msg.sender, address(this), trancheBal);
    IERC20(token).safeTransfer(_to, trancheBal * rate / ONE_TRANCHE);
```

**File:** contracts/DefaultDistributor.sol (L45-50)
```text
  function setIsActive(bool _active) external {
    require(owner() == msg.sender, '!AUTH');
    isActive = _active;
    if (_active) {
      rate = IERC20(token).balanceOf(address(this)) * ONE_TRANCHE / IERC20(trancheToken).totalSupply();
    }
```

**File:** contracts/IdleCDOEpochVariant.sol (L967-978)
```text
  function claimWithdrawRequest() external {
    // underlyings requested, here we check that user waited at least one epoch and that borrower
    // did not default upon repayment (old requests can still be claimed)
    IdleCreditVault(strategy).claimWithdrawRequest(msg.sender);
  }

  /// @notice Claim an instant withdraw request from the vault. Can be done when epoch is running
  /// as funds will get transferred from borrower when epoch starts
  function claimInstantWithdrawRequest() external {
    // Check that instant withdraws are available
    _checkNotAllowed(!allowInstantWithdraw);
    IdleCreditVault(strategy).claimInstantWithdrawRequest(msg.sender);
```
