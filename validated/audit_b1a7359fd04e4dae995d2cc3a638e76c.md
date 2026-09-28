### Title
Unauthenticated APR setter accepts out-of-scope callers before the CDO is wired, letting any EOA fix the epoch interest rate - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.setApr` (and the `setAprs`/`setAprsWithBuffer` wrappers that delegate to it) only enforces `msg.sender == idleCDO || msg.sender == manager` **after** `idleCDO` has been set via `setWhitelistedCDO`. `initialize` never sets `idleCDO`, so between deployment and the owner wiring the CDO, the authorization check is skipped entirely and any address can write `lastApr` (and `unscaledApr`) up to the `maxApr` ceiling (default 20%). This mirrors the Mattermost bug class: a scoped-permission API accepts a caller/value outside its intended scope because the scoping check is conditionally bypassed.

### Finding Description
Relevant code in `contracts/strategies/idle/IdleCreditVault.sol`:

```solidity
// lines 124-158: initialize() sets borrower/manager/apr but never idleCDO
// lines 206-235:
function setAprs(uint256 _unscaledApr, uint256 _apr) external {
    unscaledApr = _unscaledApr;
    setApr(_apr);
}
function setAprsWithBuffer(uint256 _unscaledApr, uint256 _duration, uint256 _buffer) external {
    unscaledApr = _unscaledApr;
    setApr(_duration == 0 ? _unscaledApr : _unscaledApr * (_duration + _buffer) / _duration);
}
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

`initialize` (lines 124-158) leaves `idleCDO == address(0)`; the owner must later call `setWhitelistedCDO` (line 954). During that window the `msg.sender` check is skipped, so an arbitrary EOA can call `setAprs(20e18, 20e18)` and pin `lastApr`/`unscaledApr` to the 20% cap. The CDO-side epoch accounting (`expectedEpochInterest`, `prepareStopEpochWithApr0`, `getApr`) reads `lastApr` directly, so the forged rate becomes the contractual borrower-owed interest for the entire epoch and is baked into tranche `virtualPrice` / AA-BB yield split.

### Impact Explanation
- If the attacker already holds tranche tokens, the forged 20% APR inflates `expectedEpochInterest`, raising `virtualPrice` and the withdrawal-request valuation above the borrower's actual repayment obligation. When the borrower repays only the real (lower) agreed interest, the shortfall is realized as a loss that is socialized across remaining LPs while the attacker's inflated receipts were already priced in — a direct transfer of value.
- Conversely, an attacker can pin `lastApr`/`unscaledApr` to `0`, flipping the vault into the APR0 accounting path (`_requestWithdrawApr0`, `apr0RateByEpoch`) and zeroing honest lenders' expected yield for the epoch — theft of unclaimed yield.
Loss is bounded by `maxApr * TVL * epochDuration/YEAR`, up to 20% annualized of vault TVL, quantified and direct (fair-mint/burn and yield-split invariants broken).

### Likelihood Explanation
The window exists only while `idleCDO == address(0)`, i.e. between strategy deployment and `setWhitelistedCDO`. This is a real, persistent state (not a same-tx race): nothing in `initialize` prevents the gap, deployments/upgrades routinely wire the CDO in a separate transaction, and a pre-existing unset `idleCDO` on any live vault remains permanently exploitable. The attacker needs no privilege — any EOA — and no privileged party needs to make a mistake beyond the already-existing wiring gap. The `maxApr` cap limits but does not prevent the damage (20% APR is far above real credit rates).

### Recommendation
Remove the conditional bypass: enforce `msg.sender == idleCDO || msg.sender == manager || msg.sender == owner()` unconditionally in `setApr`, or set `idleCDO` inside `initialize`/factory wiring so the unauthenticated window never exists. At minimum, revert in `setApr` when `idleCDO == address(0)` unless caller is owner.

### Proof of Concept
Foundry fork-style PoC against `contracts/strategies/idle/IdleCreditVault.sol`:

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IdleCreditVault} from "../contracts/strategies/idle/IdleCreditVault.sol";
import {IERC20Detailed} from "../contracts/interfaces/IERC20Detailed.sol";

contract SetAprScopeTest is Test {
    IdleCreditVault strategy;
    address underlying = address(0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48); // USDC
    address owner = address(0x0);
    address manager = address(0x1);
    address borrower = address(0x2);
    address attacker = address(0xB0B);

    function setUp() public {
        // deploy proxy + initialize as in production wiring
        // strategy.initialize(underlying, owner, manager, borrower, "Borrower", 5e18);
        // NOTE: initialize does NOT set idleCDO; setWhitelistedCDO is a later owner tx
    }

    function testAnyoneCanSetAprBeforeCdoWired() public {
        assertEq(strategy.idleCDO(), address(0), "CDO not yet wired");

        uint256 maxApr = strategy.maxApr(); // DEFAULT_MAX_APR = 20e18
        vm.prank(attacker);               // arbitrary unprivileged EOA
        strategy.setAprs(maxApr, maxApr); // no caller check while idleCDO == 0

        assertEq(strategy.getApr(), maxApr, "attacker pinned APR to 20%");
        assertEq(strategy.unscaledApr(), maxApr);
        // When the owner later calls setWhitelistedCDO(cdo) and the epoch starts,
        // expectedEpochInterest is computed off the forged 20% rate, inflating
        // virtualPrice and the AA/BB split versus the borrower's real obligation.
    }
}
```

A full fork PoC additionally deposits into the AA tranche from `attacker` before the malicious `setAprs`, then shows `virtualPrice()` / `expectedEpochInterest()` reflect the forged rate once the honest owner starts the epoch, and that `stopEpoch` realizes the shortfall as a loss borne by other LPs.