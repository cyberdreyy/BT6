### Title
Missing access control on `setApr`/`setAprs`/`setAprsWithBuffer` allows any EOA to set vault APR while `idleCDO` is unset - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
Like CVE-2026-23477 (an endpoint returning privileged data to any authenticated user because no role check was applied), `IdleCreditVault` exposes privileged state-mutating entry points with no caller check during the deployment window between `initialize()` and `setWhitelistedCDO()`. In `setApr`, the authorization check is explicitly skipped when `idleCDO == address(0)` (a "setup" escape hatch), so any unprivileged EOA can set `lastApr` up to `maxApr` (default `DEFAULT_MAX_APR = 20e18`, i.e. 2000% scaled APR) and set `unscaledApr` to an arbitrary value via `setAprs`/`setAprsWithBuffer`. [1](#0-0) 

### Finding Description
`setAprs` writes `unscaledApr` unconditionally and delegates the caller check to `setApr`. `setApr` only enforces `msg.sender == idleCDO || msg.sender == manager` when `idleCDO` is non-zero; the only remaining constraint is the `maxApr` cap (`0` disables the cap entirely if owner ever calls `setMaxApr(0)` before linking the CDO). `initialize()` does not set `idleCDO`; it is only wired later via `setWhitelistedCDO`. [2](#0-1) [3](#0-2) [4](#0-3) 

### Impact Explanation
An attacker observing a new `IdleCreditVault` deployment can, before the owner links the CDO, call `setAprsWithBuffer(type_max, ...)`/`setAprs(0, maxApr)` to pin `lastApr` at `20e18`. Once the CDO is linked and an epoch starts, `expectedEpochInterest()` (used by `stopEpoch`/`prepareStopEpochWithApr0` accounting) prices interest off this attacker-chosen APR: a 2000% APR forces the honest borrower to either repay vastly inflated interest or be pushed into default, and in the APR0 flow (`unscaledApr` manipulation) it corrupts `apr0RateByEpoch` interest distribution. Alternatively the attacker can set APR to 0, forcing `unscaledApr == 0` which reroutes withdraw requests into the `apr0Users` accounting path. This is a broken access-control invariant causing temporary freezing/mis-accounting of funds and, if the owner does not notice before epoch stop, direct loss via forced default or stolen yield. [5](#0-4) 

### Likelihood Explanation
Requires the deployment window between `initialize` and `setWhitelistedCDO` not being atomic (a factory may bundle them, which would reduce exposure to manual deployments only). It also requires the owner/manager to not overwrite the APR before the first `startEpoch`. The code comment "this can happen only during the setup" acknowledges the window exists by design, but nothing prevents an arbitrary caller from abusing it — the check trusts `idleCDO == 0` as proof of a benign setup phase rather than restricting to `owner`/`manager`.

### Recommendation
In `setApr`, replace the `idleCDO == 0` skip with an explicit privileged-caller check for the setup phase, e.g. `if (msg.sender != idleCDO && msg.sender != manager && msg.sender != owner()) revert NotAllowed();`, or set `idleCDO` inside `initialize()` so the escape hatch is unnecessary. Apply the same reasoning to `setAprs`/`setAprsWithBuffer`, which inherit the check transitively.

### Proof of Concept
Foundry fork PoC (attacker is any EOA; owner and manager are honest and simply deploy sequentially):

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IdleCreditVault} from "../contracts/strategies/idle/IdleCreditVault.sol";
import {IERC20Detailed} from "../contracts/interfaces/IERC20Detailed.sol";

contract SetAprMissingAuthTest is Test {
    IdleCreditVault vault;
    address owner = address(0x0A);
    address manager = address(0x0B);
    address borrower = address(0x0C);
    address attacker = address(0xBAD);

    function test_anyoneCanSetAprBeforeCDOLinked() public {
        // deploy + initialize via proxy or direct impl for test purposes
        vault = new IdleCreditVault();
        // impl has token == address(1); use a proxy in a real test — shown conceptually:
        // vault.initialize(underlying, owner, manager, borrower, "Test", 5e17);

        // Precondition: idleCDO not yet linked
        assertEq(vault.idleCDO(), address(0));

        // Attacker (unprivileged EOA) pins APR to the 2000% cap and poisons unscaledApr
        vm.prank(attacker);
        vault.setAprs(0, vault.DEFAULT_MAX_APR()); // or setAprsWithBuffer(type(uint).max,...)
        assertEq(vault.lastApr(), 20e18);
        assertEq(vault.unscaledApr(), 0); // forces APR0 withdraw accounting path

        // Expected: revert NotAllowed. Actual: succeeds → poisoned epoch interest accounting.
    }
}
```

Caveat: I could not verify in this pass whether `IdleCreditVaultFactory` calls `initialize` and `setWhitelistedCDO` atomically in the same transaction; if it does, the practical exposure narrows to manual/non-atomic deployments, but the missing access control in `setApr` remains a code-level defect.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L124-158)
```text
  function initialize(
    address _underlyingToken,
    address _owner,
    address _manager,
    address _borrower,
    string memory borrowerName,
    uint256 _apr
  ) public virtual initializer {
    OwnableUpgradeable.__Ownable_init();
    ReentrancyGuardUpgradeable.__ReentrancyGuard_init();
    require(token == address(0), "Token is already initialized");

    //----- // -------//
    token = _underlyingToken;
    underlyingToken = IERC20Detailed(token);
    tokenDecimals = underlyingToken.decimals();
    oneToken = 10**(tokenDecimals);
    borrower = _borrower;
    manager = _manager;
    maxApr = DEFAULT_MAX_APR;
    // on the first setup we set the lastApr equal to the unscaledApr
    lastApr = _apr;
    unscaledApr = _apr;
    defaultRecoveryInitialized = true;

    // name will be like: Pareto Credit Vault Borrower
    // symbol will be like: Borrower
    ERC20Upgradeable.__ERC20_init(
      _concat(string("Pareto Credit Vault "), borrowerName),
      borrowerName
    );
    //------//-------//

    transferOwnership(_owner);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L206-235)
```text
  function setAprs(uint256 _unscaledApr, uint256 _apr) external {
    unscaledApr = _unscaledApr;
    // here we also check that msg.sender is allowed
    setApr(_apr);
  }

  /// @notice set both the unscaled APR and APR scaled by epoch plus buffer duration.
  /// @dev only CDO and manager can set the APR through `setApr`.
  /// @param _unscaledApr unscaled APR
  /// @param _duration epoch duration
  /// @param _buffer buffer duration
  function setAprsWithBuffer(uint256 _unscaledApr, uint256 _duration, uint256 _buffer) external {
    unscaledApr = _unscaledApr;
    setApr(_duration == 0 ? _unscaledApr : _unscaledApr * (_duration + _buffer) / _duration);
  }

  /// @notice set the fixed apr
  /// @dev only cdo and manager can set the apr. If manager manually set apr from 
  /// here it will not be scaled to include the buffer period
  function setApr(uint256 _apr) public {
    address _cdo = idleCDO;

    // if cdo is not yet set we skip the check (this can happen only during the setup)
    if (_cdo != address(0)) {
      if (msg.sender != _cdo && msg.sender != manager) revert NotAllowed();
    }
    uint256 _maxApr = maxApr;
    if (_maxApr != 0 && _apr > _maxApr) revert NotAllowed();
    lastApr = _apr;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L490-541)
```text
  function prepareStopEpochWithApr0(uint256 _interest) external returns (uint256 _expInterest, uint256 _adjPendingWithdrawFees) {
    _onlyIdleCDO();
    IIdleCDOEpochVariant _cdo = IIdleCDOEpochVariant(idleCDO);
    uint256 _pendingFees = _cdo.pendingWithdrawFees();
    uint256 _tvl = _cdo.getContractValue();
    _expInterest = _interest > 1 ? _interest : _cdo.expectedEpochInterest();
    _adjPendingWithdrawFees = _pendingFees;
    // Principal currently waiting for withdraw that was requested while APR was 0,
    // net of the upfront management fee charged at request time.
    uint256 _principal = apr0TotalPrincipal;

    // Fast path: no APR0 accounting needed.
    if (_principal == 0) {
      return (_expInterest, _adjPendingWithdrawFees);
    }
    // APR0 principal is only valid while APR is 0 for that request lifecycle.
    if (unscaledApr != 0) {
      revert NotAllowed();
    }

    uint256 _apr0NetInterest;
    // APR0 allocation is computed only when stopEpoch receives a real override interest.
    // _expectedInterest == 1 is the "request all funds back" sentinel and is handled in IdleCDO.
    if (_expInterest > 1 && _expInterest > _pendingFees) {
      // Remove already booked withdraw fees from the interest base before splitting.
      uint256 _interestNetOfFees = _expInterest - _pendingFees;
      // Total principal used for the pro-rata split:
      // IdleCDO TVL (which excludes APR0 requested principal) + APR0 principal bucket.
      uint256 _totalPrincipalForSplit = _tvl + _principal;
      if (_totalPrincipalForSplit != 0) {
        // APR0 users get a pro-rata share of realized interest.
        uint256 _apr0InterestGross = _interestNetOfFees * _principal / _totalPrincipalForSplit;
        if (_apr0InterestGross != 0) {
          // Same fee model as normal withdraw interest.
          uint256 _apr0Fee = _apr0InterestGross * _cdo.fee() / FULL_ALLOC;
          _apr0NetInterest = _apr0InterestGross - _apr0Fee;
          _adjPendingWithdrawFees += _apr0Fee;
          _expInterest -= _apr0NetInterest;
        }
      }
    }

    // Finalize one-epoch APR0 interest for current epoch only.
    if (_apr0NetInterest != 0) {
      // Funds owed to withdraw requesters increase by APR0 net interest.
      pendingWithdraws += _apr0NetInterest;
      // Save per-epoch net rate; each APR0 request accrues exactly once on its request epoch.
      apr0RateByEpoch[epochNumber] = (_apr0NetInterest * 1e18) / _principal;
    }
    // Close current APR0 bucket so it cannot accrue again on later stopEpoch calls.
    apr0TotalPrincipal = 0;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L954-957)
```text
  function setWhitelistedCDO(address _cdo) external onlyOwner {
    require(_cdo != address(0), "IS_0");
    idleCDO = _cdo;
  }
```
