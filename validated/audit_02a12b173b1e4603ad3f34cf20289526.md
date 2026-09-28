### Title
Missing recipient validation in `DefaultDistributor.claim` burns tranche tokens while sending recovery funds to the zero address - (File: contracts/DefaultDistributor.sol)

### Summary
`DefaultDistributor.claim(address _to)` pulls the caller's entire tranche-token balance into the contract and transfers the proportional share of recovered underlying to `_to`, but never validates `_to`. If `_to == address(0)` (or another unrecoverable address), the user's tranche tokens are consumed and the underlying is sent to an inaccessible address — a direct, permanent loss, identical in class to the referenced `FETH.withdrawFrom` finding.

### Finding Description
After a borrower default, `IdleCDOEpochVariant` finalizes recovery by deploying a `DefaultDistributor` per tranche class, funding it with the recovered underlying, and activating claims via `setIsActive`, which fixes `rate = underlying balance / trancheToken totalSupply` [1](#0-0) .

`claim` then executes:

```solidity
// contracts/DefaultDistributor.sol
function claim(address _to) external {
  require(isActive, '!ACTIVE');
  IERC20 tranche = IERC20(trancheToken);
  uint256 trancheBal = tranche.balanceOf(msg.sender);
  tranche.safeTransferFrom(msg.sender, address(this), trancheBal);
  IERC20(token).safeTransfer(_to, trancheBal * rate / ONE_TRANCHE);
}
``` [2](#0-1) 

Two compounding facts make this worse than a typical zero-check omission:
- `_to` is fully user-supplied with no `!= address(0)` guard and no check that `_to != address(this)` (the latter at least leaves funds rescuable via `transferToken` [3](#0-2) ).
- The function claims the caller's *entire* `trancheBal` in one shot, so a single bad `_to` forfeits the holder's complete recovery entitlement, not a partial amount.

The burn-then-pay sequence is irreversible: tranche tokens are already escrowed when `safeTransfer(_to, ...)` sends underlying to `0x0` (OpenZeppelin ERC20 `transfer` to zero does not revert — it just moves the tokens to an unspendable address).

### Impact Explanation
Permanent loss of 100% of a defaulted-tranche holder's recovery proceeds. Quantified loss: `trancheBal * rate / ONE_TRANCHE` of underlying — the claimant's full pro-rata share of the recovery pool — becomes unrecoverable, while their tranche tokens are simultaneously burned. This breaks the "one receipt, one payout" invariant: the receipt is consumed with no usable payout. The same user-error fund-loss scenario was confirmed and fixed by the Foundation team in the referenced M-09 report.

### Likelihood Explanation
Requires a caller (or their integrating UI/relayer) to pass `address(0)` — or a contract address that cannot handle the underlying — as `_to`. The likelihood driver is identical to the judged-medium original: defaults are rare, high-stress events where claimants may interact through custom front-ends or batch scripts, and a single misconfigured parameter loses the entire claim with no recourse. Notably, funds sent to `address(this)` or the tranche token contract are rescuable by the owner via `transferToken`, but `address(0)` is not.

### Recommendation
Validate `_to` at the top of `claim`:

```solidity
require(_to != address(0) && _to != address(this) && _to != token && _to != trancheToken, '!TO');
```

### Proof of Concept
Foundry fork-style PoC (assume a fork where a `DefaultDistributor` is active, or deploy the real contract in a unit test):

```solidity
function testClaimToZeroAddress() public {
  // setup: distributor funded with `recovered` underlying, rate set
  DefaultDistributor dist = new DefaultDistributor(address(underlying), address(tranche), owner);
  deal(address(underlying), address(dist), 1000e18);
  vm.prank(owner);
  dist.setIsActive(true); // rate = 1000e18 * 1e18 / tranche.totalSupply()

  // holder of tranche tokens approves and claims, mistakenly passing address(0)
  address holder = ...; // tranche token holder post-default
  vm.startPrank(holder);
  tranche.approve(address(dist), type(uint256).max);
  dist.claim(address(0));
  vm.stopPrank();

  // holder lost everything: tranche tokens escrowed, underlying burned to 0x0
  assertEq(tranche.balanceOf(holder), 0);
  assertGt(underlying.balanceOf(address(0)), 0);
}
```

Expected: the call succeeds, holder's tranche balance is zeroed, and the underlying payout sits at `address(0)` — permanently lost. After the fix, `dist.claim(address(0))` reverts before any state change.

### Citations

**File:** contracts/DefaultDistributor.sol (L35-41)
```text
  function claim(address _to) external {
    require(isActive, '!ACTIVE');
    IERC20 tranche = IERC20(trancheToken);
    uint256 trancheBal = tranche.balanceOf(msg.sender);
    tranche.safeTransferFrom(msg.sender, address(this), trancheBal);
    IERC20(token).safeTransfer(_to, trancheBal * rate / ONE_TRANCHE);
  }
```

**File:** contracts/DefaultDistributor.sol (L45-51)
```text
  function setIsActive(bool _active) external {
    require(owner() == msg.sender, '!AUTH');
    isActive = _active;
    if (_active) {
      rate = IERC20(token).balanceOf(address(this)) * ONE_TRANCHE / IERC20(trancheToken).totalSupply();
    }
  }
```

**File:** contracts/DefaultDistributor.sol (L57-60)
```text
  function transferToken(address _token, address _to, uint256 _value) external {
    require(owner() == msg.sender, '!AUTH');
    IERC20(_token).safeTransfer(_to, _value);
  }
```
