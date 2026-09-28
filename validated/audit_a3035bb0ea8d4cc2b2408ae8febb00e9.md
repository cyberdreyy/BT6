### Title
Unprivileged APR manipulation before CDO linking can inflate epoch interest and force borrower insolvency - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.setApr` skips its caller authorization check whenever `idleCDO` has not yet been configured. During the window between strategy initialization and `setWhitelistedCDO`, any EOA can overwrite `lastApr` up to `DEFAULT_MAX_APR`. The next honest `startEpoch` uses that attacker-selected APR to calculate `expectedEpochInterest`, causing excess borrower debt and potentially a default or loss socialization.

### Finding Description
`setApr` normally restricts callers to the configured CDO or manager, but the check is entirely bypassed when `idleCDO == address(0)`:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol
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

An attacker can call `setApr(DEFAULT_MAX_APR)` after `initialize` but before the owner links the CDO. When `IdleCDOEpochVariant.startEpoch` later runs, it calls `_calcInterest(getContractValue())`, which reads the strategy's current APR through `getApr`, and stores the result as `expectedEpochInterest`. The borrower is then expected to return principal plus this inflated interest.

The same authorization-free setup window is documented by `setApr`'s own comment. `setAprs` and `setAprsWithBuffer` are also reachable, although they ultimately route through the same `setApr` check.

### Impact Explanation
For a fixed-APR epoch vault, the attacker can be an ordinary KYC-passing lender/tranche holder and inflate the epoch's contractual yield.

Example:

- Vault TVL: `1,000,000` underlying units.
- Epoch duration: 30 days.
- Intended APR: `5%`.
- `DEFAULT_MAX_APR`: `20e18`.
- Excess APR: `15%` annualized.
- Excess expected interest for 30 days: approximately `12,329` underlying units.

If the borrower has only provisioned repayment for the intended APR, the inflated expected interest can make the stop-epoch transfer fail and push the vault into the default path. If the borrower does pay, the excess is distributed as yield; the attacker receives a proportional share and can combine the attack with a large deposit to capture most of it. If the borrower cannot pay, the loss can be crystallized through the normal default/recovery process, harming LP principal rather than remaining isolated to the attacker's position.

The broken invariant is access control over a privileged accounting parameter. The `maxApr` cap limits severity but does not prevent the unauthorized change.

### Likelihood Explanation
The exploit requires deployment/configuration sequencing:

1. `IdleCreditVault.initialize` completes.
2. `idleCDO` remains unset.
3. The attacker submits `setApr`.
4. Owner later calls `setWhitelistedCDO` and owner/manager starts an epoch without correcting `lastApr`.

This is not exploitable after CDO linking, so likelihood is lower than a permanently unprotected endpoint. However, proxy-based deployment and operational setup create a real race window, and the contract explicitly tolerates unauthenticated APR updates during that window. Existing guards do not prevent it: `onlyOwner` is absent, `manager` is not required while `idleCDO == 0`, and the only bound is `maxApr`.

### Recommendation
Do not skip authorization when `idleCDO` is unset. For example:

```solidity
function setApr(uint256 _apr) public {
  address _cdo = idleCDO;
  if (_cdo == address(0)) {
    if (msg.sender != owner() && msg.sender != manager) revert NotAllowed();
  } else {
    if (msg.sender != _cdo && msg.sender != manager) revert NotAllowed();
  }

  uint256 _maxApr = maxApr;
  if (_maxApr != 0 && _apr > _maxApr) revert NotAllowed();
  lastApr = _apr;
}
```

Alternatively, set the initial APR only during `initialize`, remove public APR mutation before CDO linking, or make `setWhitelistedCDO` and the initial APR configuration atomic through the factory/bootstrapper.

### Proof of Concept
A Foundry PoC can reproduce the issue by initializing `IdleCreditVault`, having an attacker set the APR before `setWhitelistedCDO`, then letting the owner link the CDO and manager start the epoch.

```solidity
function test_UnprivilegedAprUpdateBeforeCdoLink() external {
    // Strategy is initialized with an intended APR.
    vm.prank(owner);
    strategy.initialize(
        address(underlying),
        owner,
        manager,
        borrower,
        "Borrower",
        5e18
    );

    assertEq(strategy.idleCDO(), address(0));
    assertEq(strategy.getApr(), 5e18);

    // Any EOA can overwrite lastApr while idleCDO is unset.
    address attacker = makeAddr("attacker");
    vm.prank(attacker);
    strategy.setApr(strategy.DEFAULT_MAX_APR());

    assertEq(strategy.getApr(), strategy.DEFAULT_MAX_APR());

    // Honest owner links the CDO without resetting APR.
    vm.prank(owner);
    strategy.setWhitelistedCDO(address(cdoEpoch));

    // Seed TVL and let the honest manager start the epoch.
    deal(address(underlying), address(this), 1_000_000e6);
    underlying.approve(address(cdoEpoch), 1_000_000e6);
    cdoEpoch.depositAA(1_000_000e6);

    vm.warp(cdoEpoch.epochEndDate() + cdoEpoch.bufferPeriod() + 1);
    vm.prank(manager);
    cdoEpoch.startEpoch();

    // Expected interest was computed using the attacker's 20% APR,
    // not the initialized 5% APR.
    assertGt(
        cdoEpoch.expectedEpochInterest(),
        expectedInterestAtFivePercent
    );
}
```

The PoC should assert both the unauthorized state transition and the inflated `expectedEpochInterest`; extending it through `stopEpoch` demonstrates either excess borrower payment or entry into the default path when repayment allowance/liquidity covers only the intended interest.