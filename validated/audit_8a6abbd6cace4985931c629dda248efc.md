### Title
Unauthenticated `setApr`/`setAprs`/`setAprsWithBuffer` on `IdleCreditVault` lets any EOA overwrite epoch APR before `idleCDO` is wired — (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault.setApr()` enforces caller authorization only when `idleCDO != address(0)`. During the window between `initialize()` and the owner's `setWhitelistedCDO()` call, `lastApr` and `unscaledApr` can be written by any address — a direct analog of CVE-2019-10166, where a mutating API (`virDomainManagedSaveDefineXML`) was left callable by readonly clients. An unprivileged attacker can set the APR to `maxApr` (default `DEFAULT_MAX_APR = 20e18`), which is then used verbatim by `IdleCDOEpochVariant.startEpoch()` to compute `expectedEpochInterest`. At `stopEpoch` the borrower is asked to repay the inflated interest; a normal honest borrower cannot cover it, the `getFundsFromBorrower` self-call throws, and `_handleBorrowerDefault` fires — freezing deposits/withdrawals and forcing all lenders through the `finalizeDefault` recovery haircut.

### Finding Description
In `contracts/strategies/idle/IdleCreditVault.sol`:

- `setApr` only checks the caller if `idleCDO` is already set:
```solidity
function setApr(uint256 _apr) public {
  address _cdo = idleCDO;
  if (_cdo != address(0)) {
    if (msg.sender != _cdo && msg.sender != manager) revert NotAllowed();
  }
  uint256 _maxApr = maxApr;
  if (_maxApr != 0 && _apr > _maxApr) revert NotAllowed();
  lastApr = _apr;
}
```
- `setAprs` (line 206) and `setAprsWithBuffer` (line 217) write `unscaledApr` and delegate to `setApr`, inheriting the same gap. The writes inside `setAprs*` are only rolled back when `setApr` reverts — which it does not when `idleCDO == 0`, so an EOA can set `lastApr`/`unscaledApr` to anything up to `maxApr` (20e18 by default, line 89/143).

In `contracts/IdleCDOEpochVariant.sol`:

- `startEpoch()` (line 260) computes `expectedEpochInterest = pendingWithdrawFees + _calcInterest(getContractValue())`, and `_calcInterest` (line 800) uses `_getStrategyApr()` = `lastApr` — the attacker-controlled value.
- `stopEpoch()` pulls `expectedEpochInterest + pendingWithdraws` from the borrower via `try this.getFundsFromBorrower(...)` (line 408). With APR forced to `20e18`, the expected interest for a 30-day epoch is roughly `TVL * 20e18/100 * 30/365 / 1e18 ≈ 16% of TVL` — far above any real negotiated rate — so an honest borrower's repayment `transferFrom` fails and the catch block calls `_handleBorrowerDefault` (line 504).
- `_handleBorrowerDefault` (line 577) sets `defaulted = true`, pauses deposits, stops the epoch, and disables withdraw requests. Recovery then flows only through `finalizeDefault` (line 194) → `finalizeDefaultRecovery` in the strategy (line 661), where all active and pending claims are paid at `defaultRecoveryPrice` — a haircut on every lender.

Guards that do not stop this: `maxApr` caps but does not prevent the write; `isWalletAllowed`/Keyring gating applies to deposits/withdraw requests, not `setApr`; `_onlyIdleCDO` is not used here; the owner comment explicitly accepts the `idleCDO == 0` skip for setup, making the exposure concrete whenever `setWhitelistedCDO` is executed in a later transaction than `initialize` (the standard factory/initializer ordering).

### Impact Explanation
Direct forced default with lender losses. The attacker (any EOA, no KYC, no tokens) causes the honest borrower to under-deliver at `stopEpoch`, triggering `_handleBorrowerDefault`. Consequences: (1) all deposits and withdrawal requests permanently frozen until owner/manager `finalizeDefault`; (2) all active LPs and pending receipt holders are paid at `defaultRecoveryPrice < 1e18` — a quantified haircut equal to the shortfall between recovered funds and `totalBasis`; (3) fees are zeroed (`fee = 0`, `managementFee = 0`, `unclaimedFees = 0` in `finalizeDefault`), permanently destroying accrued unclaimed yield. Broken invariant: only owner/manager/CDO may influence epoch interest accounting.

### Likelihood Explanation
Exploitation requires a deployment window where `initialize` has run but `idleCDO` is still unset (i.e., `setWhitelistedCDO` not yet called or executed in a separate tx — a common upgradeable-deploy pattern), and a value is not subsequently overwritten by a legitimate `setAprsWithBuffer` from the CDO before the first `startEpoch`. The attacker can monitor mempool/deployments and set `lastApr`/`unscaledApr` immediately after `initialize`. Once set, the poisoned APR survives until a privileged caller explicitly overwrites it — nothing in `setWhitelistedCDO`, `setEpochParams`, or `startEpoch` re-validates `lastApr` against a signed borrower rate. Likelihood is moderate: it hinges on deployment sequencing rather than any privileged misbehavior, and the fix cost is trivial, but the impact on first-epoch vaults is a full forced default.

### Recommendation
Always enforce authorization in `setApr`, `setAprs`, and `setAprsWithBuffer`: require `msg.sender == idleCDO || msg.sender == manager || (idleCDO == address(0) && msg.sender == owner())`, or move the initial APR into `initialize` and delete the `idleCDO == 0` skip entirely. Additionally, have `startEpoch` (or `setWhitelistedCDO`) sanity-check `lastApr` against a stored owner-approved rate before computing `expectedEpochInterest`.

### Proof of Concept
Foundry fork test outline (anvil mainnet fork, real USDC):

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;
import "forge-std/Test.sol";
import {IdleCreditVault} from "../contracts/strategies/idle/IdleCreditVault.sol";
import {IdleCDOEpochVariant} from "../contracts/IdleCDOEpochVariant.sol";

contract SetAprHijackTest is Test {
  IdleCreditVault strategy;
  IdleCDOEpochVariant cdo;
  address attacker = address(0xBABE);
  address lender   = address(0xAAAA);

  function test_AprHijackBeforeCDOWired() public {
    // 1. Deployer initializes strategy; setWhitelistedCDO NOT yet called
    strategy = new IdleCreditVault();
    strategy.initialize(USDC, owner, manager, borrower, "Borrower", 10e18);

    // 2. Attacker front-runs wiring: sets APR to max (no auth while idleCDO==0)
    vm.prank(attacker);
    strategy.setAprs(20e18, 20e18);
    assertEq(strategy.lastApr(), 20e18);

    // 3. Owner wires CDO and lenders deposit (AA/BB) during buffer
    vm.prank(owner);
    strategy.setWhitelistedCDO(address(cdo));
    // ... lender deposits via depositAA/depositBB ...

    // 4. Manager honestly calls startEpoch()
    vm.prank(manager);
    cdo.startEpoch();
    // expectedEpochInterest ~= TVL * 16% instead of negotiated ~1%

    // 5. At epoch end, honest borrower repays only real interest;
    //    getFundsFromBorrower(inflated) transferFrom reverts -> default
    vm.warp(cdo.epochEndDate() + 1);
    vm.prank(manager);
    cdo.stopEpoch(10e18, 0);
    assertTrue(cdo.defaulted());          // forced default
    assertTrue(cdo.paused());             // deposits frozen
    // lenders now only recover via finalizeDefault at recoveryPrice < 1
  }
}
```

Note: severity is bounded by `maxApr` (default 20e18 scaled ≈ 16% of TVL per 30-day epoch) and requires the pre-wiring window; if deployments always atomically call `initialize` + `setWhitelistedCDO` in one transaction, the practical exploitability drops to upgrade/reconfiguration paths where `idleCDO` could be transiently unset.