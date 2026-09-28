### Title
Missing zero-address check on claim recipient causes permanent loss of default-recovery funds - (File: contracts/DefaultDistributor.sol)

### Summary
`DefaultDistributor.claim` pulls all of the caller's tranche tokens into the distributor and transfers the caller's proportional share of recovered underlyings to a user-supplied `_to` address. There is no `address(0)` validation on `_to`, mirroring the reported bug class (unchecked address parameter on a fund-transferring path). If `_to == address(0)` and the underlying token does not itself revert on transfers to the zero address (e.g. USDT, which does not check the recipient), the recovered underlyings are permanently burned while the tranche tokens are still pulled into the distributor — the claim is consumed with zero compensation. [1](#0-0) 

### Finding Description
The contract is deployed by the CDO after a borrower default to redistribute recovered funds pro-rata to tranche holders. `claim` is the sole payout path: [1](#0-0) 

```solidity
function claim(address _to) external {
  require(isActive, '!ACTIVE');
  IERC20 tranche = IERC20(trancheToken);
  uint256 trancheBal = tranche.balanceOf(msg.sender);
  tranche.safeTransferFrom(msg.sender, address(this), trancheBal);
  IERC20(token).safeTransfer(_to, trancheBal * rate / ONE_TRANCHE);
}
```

`_to` is taken verbatim. Unlike other fund-moving entry points in the codebase that explicitly guard the recipient — e.g. `IdleCreditVaultWriteOffEscrow.emergencyWithdraw` reverts `Is0()` on a zero `_to` and `setFeeReceiver` rejects `address(0)` — `claim` performs no such check. [2](#0-1) 

The burn is irreversible because:
- `rate` is fixed at activation from the full distributor balance, so the lost underlyings are not redistributed to other claimants; they sit at `address(0)` forever. [3](#0-2) 
- Tranche tokens transferred in are not returnable to the claimant — `claim` has already consumed them, and `transferToken` is owner-only emergency tooling that should not be relied on for routine mistakes. [4](#0-3) 

Note the same missing check applies to the constructor's `_token`, `_trancheToken`, and `_owner` parameters, though a zero `_token`/`_trancheToken` would fail loudly on the first external call rather than silently lose funds. [5](#0-4) 

### Impact Explanation
Permanent loss of the claimant's entire share of post-default recovery funds. In a default scenario the recovery may represent the only remaining value for senior/junior tranche holders, so a single mistaken `claim(address(0))` destroys up to 100% of that user's claimable underlyings with no recourse, while their tranche tokens are already escrowed in the distributor.

### Likelihood Explanation
Medium-low. It requires a caller mistake or a buggy frontend/integration passing a zero address, which is precisely the scenario zero-address checks exist to catch — the same class the external report flags in `FarmingPool`'s constructor. It cannot be triggered by a third party against a victim, which lowers severity relative to active theft, but the loss is total and the fix is one line. Additionally, whether the loss materializes depends on the underlying token: tokens that revert on `transfer` to `address(0)` (OZ-style implementations) would just revert the whole call; tokens that permit it (USDT and similar) produce the silent burn.

### Recommendation
Add an explicit recipient check in `claim`:

```solidity
function claim(address _to) external {
  require(isActive, '!ACTIVE');
  require(_to != address(0), '0_ADDR');
  ...
}
```

Optionally also validate `_token`, `_trancheToken`, and `_owner != address(0)` in the constructor for defense-in-depth, consistent with `IdleCreditVaultWriteOffEscrow`'s `Is0` pattern.

### Proof of Concept
Foundry test sketch (mock ERC20 that allows transfers to `address(0)`, i.e. USDT-like):

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {DefaultDistributor} from "../contracts/DefaultDistributor.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

// USDT-like: overrides OZ zero-recipient revert by using a raw implementation
contract PermissiveToken {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;
    function mint(address to, uint256 amt) external { balanceOf[to] += amt; }
    function approve(address s, uint256 a) external returns (bool) { allowance[msg.sender][s] = a; return true; }
    function transfer(address to, uint256 amt) external returns (bool) {
        balanceOf[msg.sender] -= amt; balanceOf[to] += amt; return true; // no zero check
    }
    function transferFrom(address f, address t, uint256 a) external returns (bool) {
        allowance[f][msg.sender] -= a; balanceOf[f] -= a; balanceOf[t] += a; return true;
    }
}

contract ClaimZeroTest is Test {
    function test_claimToZeroBurnsFunds() public {
        PermissiveToken underlying = new PermissiveToken();
        PermissiveToken tranche = new PermissiveToken();
        DefaultDistributor dist = new DefaultDistributor(
            address(underlying), address(tranche), address(this));

        address alice = address(0xA11CE);
        tranche.mint(alice, 100e18);          // alice holds all tranches
        underlying.mint(address(dist), 50e18); // recovery funds deposited

        dist.setIsActive(true);               // rate = 0.5 underlying per tranche

        vm.prank(alice);
        tranche.approve(address(dist), type(uint256).max);
        vm.prank(alice);
        dist.claim(address(0));               // mistaken zero recipient

        assertEq(underlying.balanceOf(address(0)), 50e18); // funds burned at zero
        assertEq(underlying.balanceOf(alice), 0);
        assertEq(tranche.balanceOf(alice), 0);             // tranches consumed anyway
    }
}
```

The test demonstrates the claim is consumed and the recovery underlyings end up at `address(0)` with no compensating transfer to the holder.

### Citations

**File:** contracts/DefaultDistributor.sol (L26-30)
```text
  constructor(address _token, address _trancheToken, address _owner) {
    token = _token;
    trancheToken = _trancheToken;
    transferOwnership(_owner);
  }
```

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

**File:** contracts/IdleCreditVaultWriteOffEscrow.sol (L164-179)
```text
  function setFeeReceiver(address _feeReceiver) external {
    _checkOnlyOwner();
    if (_feeReceiver == address(0)) revert Is0();
    feeReceiver = _feeReceiver;
  }

  /// @notice emergency withdraw function to allow the owner to withdraw tokens from the contract
  /// @param _token address of the token to withdraw
  /// @param _to address to withdraw the tokens to
  /// @param _amount amount of tokens to withdraw
  function emergencyWithdraw(address _token, address _to, uint256 _amount) external {
    _checkOnlyOwner();
    // do not allow to withdraw to the zero address
    if (_to == address(0)) revert Is0();

    IERC20Detailed(_token).safeTransfer(_to, _amount);
```
