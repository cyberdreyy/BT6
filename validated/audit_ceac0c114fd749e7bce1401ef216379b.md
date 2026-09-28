### Title
IdleCreditVault assumes fee‑on‑transfer tokens move 1:1, inflating strategy shares and recovery reserves - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`IdleCreditVault` uses the requested/expected `_amount` for minting strategy tokens, recording collected withdraw funds, and sizing the default‑recovery reserve — it never measures actual `balanceOf` deltas. If the vault's underlying token charges a transfer fee (e.g. USDT with the fee switch enabled, or any rebasing/FoT pool currency), every pull or push of underlying settles for less than the accounted amount. This is the same bug class as Notional's `expectedUnderlying` check: accounting is done on nominal amounts rather than received amounts, producing minted liabilities that exceed real backing and, ultimately, unpaid claims.

### Finding Description
Three paths assume exact amounts:

1. `deposit(uint256 _amount)` calls `underlyingToken.safeTransferFrom(msg.sender, address(this), _amount)` and then unconditionally `_mint(msg.sender, _amount)` [1](#0-0) . With a fee-on-transfer underlying the vault receives `_amount - fee` but mints `_amount` shares to the IdleCDO, so `balanceOf(idleCDO)` strategy tokens overstate real backing by the fee on every deposit.
2. `collectWithdrawFunds(_amount)` and `collectInstantWithdrawFunds(_amount)` pull `_amount` from the CDO while recording `pendingWithdraws`/`lossRecoveryPriceByEpoch` against the nominal value [2](#0-1) . The funded receipt pool is therefore short by the fee.
3. `finalizeDefaultRecovery` computes `reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve` and `recoveryPrice` from nominal amounts, then pulls `_recoveredAmount` via `safeTransferFrom` [3](#0-2) . The recorded `defaultRecoveryReserve` exceeds actual holdings.

Claims then pay out using nominal `_amount` in `_transferFundedClaim` and `_transferDefaultRecovery`, and `defaultRecoveryReserve -= _amount` decrements by the full claim [4](#0-3) . Because the reserve was credited for more than was ever received, the last claimants' `safeTransfer` reverts on insufficient balance, permanently freezing their recovery.

Note also that `deposit` is called by the CDO after the CDO itself pulled tokens from the depositor, so a fee is charged twice (user→CDO, CDO→vault) while shares are minted once at the gross amount, amplifying the shortfall.

### Impact Explanation
- **Insolvency / unfair mint**: every deposit mints strategy tokens (1:1 claims on underlying) exceeding underlying received. Tranche holders redeeming first drain real funds; later withdrawers and receipt holders absorb a shortfall equal to cumulative fees — direct loss proportional to `fee% × totalDeposits` per epoch cycle.
- **Permanent freezing of unclaimed yield/recovery**: `defaultRecoveryReserve` and `lossRecoveryPriceByEpoch` funded amounts are overstated by the fee, so the final `claimWithdrawRequest`/`_claimDefaultedWithdrawRequest`/`_transferDefaultRecovery` calls revert, locking those users' funds in the vault forever.
- Unlike Notional (which needed a `require` to revert), here the failure is silent: accounting succeeds but backing is short, surfacing only when the insolvency is realized at claim time — a worse outcome since it cannot be detected by a single transaction.

### Likelihood Explanation
The underlying token is fixed at `initialize` by the honest owner, so this requires the deployment to use a fee-capable token. USDT — a standard credit/pool currency supported by these Pareto/Clearpool-style vaults — implements a configurable transfer fee that Tether can enable non-atomically. No attacker privilege is needed: any KYC-passed lender depositing (or the honest borrower funding `collectWithdrawFunds`/finalization) triggers the miscounting; the attacker only needs to be an early claimant to crystallize the loss onto later claimants. Severity is medium for the same reasons as the original finding: conditional on the token choice, but deterministic and fund-impacting once active.

### Recommendation
Measure actual received amounts instead of trusting parameters:
- In `deposit`, `collectWithdrawFunds`, `collectInstantWithdrawFunds` and `finalizeDefaultRecovery`, snapshot `underlyingToken.balanceOf(address(this))` before/after the `safeTransferFrom` and use the delta for minting, pending-withdraw funding, and `defaultRecoveryReserve`/`recoveryPrice` accounting.
- In `requestWithdraw`-time pulls done by `IdleCDOEpochVariant`, similarly account the received delta, or reject known fee-on-transfer underlyings at `initialize` (e.g. document/whitelist) if support is not intended.
- Apply the same fix in `sendInterestAndDeposits`/claim paths if the CDO ever pulls back received amounts.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IdleCreditVault} from "../contracts/strategies/idle/IdleCreditVault.sol";
import {FeeOnTransferToken} from "./mocks/FeeOnTransferToken.sol"; // 1% fee ERC20

contract FoTVaultTest is Test {
    IdleCreditVault vault;
    FeeOnTransferToken fot;
    address cdo = makeAddr("cdo");      // stands in for IdleCDOEpochVariant
    address alice = makeAddr("alice");
    address bob = makeAddr("bob");

    function setUp() public {
        fot = new FeeOnTransferToken(100); // 1% fee
        vault = new IdleCreditVault();
        vault.initialize(address(fot), address(this), makeAddr("manager"), makeAddr("borrower"), "B", 0);
        vault.setWhitelistedCDO(cdo);
        fot.mint(cdo, 1_000e6);
    }

    function testDepositOverMintsShares() public {
        // CDO pulls 100e6 from lender, pays 1% fee -> CDO holds 99e6
        fot.mint(alice, 100e6);
        vm.prank(alice); fot.transfer(cdo, 100e6);
        // CDO deposits its 99e6 to the vault; vault receives ~98.01e6
        vm.startPrank(cdo);
        fot.approve(address(vault), type(uint256).max);
        uint256 bal = fot.balanceOf(cdo);          // 99e6
        vault.deposit(bal);
        // vault minted `bal` shares but holds less
        assertEq(vault.balanceOf(cdo), bal);
        assertLt(fot.balanceOf(address(vault)), bal);
        // solvency broken: shares > backing
        assertLt(fot.balanceOf(address(vault)), vault.balanceOf(cdo));
    }

    function testRecoveryReserveOverstated() public {
        // borrower/recovery source funds _recoveredAmount = 100e6 with 1% fee
        // finalizeDefaultRecovery records reserveAmount = 100e6 but vault receives 99e6
        // last claimant's _transferDefaultRecovery reverts -> funds permanently frozen
    }
}
```

The PoC deploys `IdleCreditVault` against a 1% fee-on-transfer ERC20 (a USDT-style token on a mainnet fork works identically), calls `deposit` via the `idleCDO`, and asserts `balanceOf(idleCDO) > underlyingToken.balanceOf(vault)` — i.e., minted claims exceed real backing. A second test funds `finalizeDefaultRecovery`/`collectWithdrawFunds` with the same token and shows the final `_transferDefaultRecovery`/`_transferFundedClaim` reverts, permanently freezing the last claimant's funds.

Caveat: I was unable to trace `IdleCDOEpochVariant._deposit`/`stopEpoch` call sites in detail within the available iterations, so the exact upstream mint path is inferred from `deposit`'s `_onlyIdleCDO` guard and the `mintStrategyTokens` companion function.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L411-429)
```text
  function collectWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    uint256 pendingBasis = pendingWithdraws;
    if (_amount < pendingBasis) {
      // Legacy receipts do not have per-epoch ownership data, so they can only be fully funded.
      if (!defaultRecoveryInitialized) revert NotAllowed();
      uint256 lossRecoveryPrice = _amount * RECOVERY_FULL / pendingBasis;
      // Avoid storing a zero price, which is indistinguishable from "no loss-adjusted epoch".
      if (lossRecoveryPrice == 0) revert NotAllowed();
      pendingWithdraws = 0;
      lossRecoveryPriceByEpoch[epochNumber] = lossRecoveryPrice;
    } else {
      // A plain implementation upgrade may leave legacy normal receipts pending. Their next
      // successful stop can fully fund the aggregate before lazy initialization occurs.
      pendingWithdraws = pendingBasis - _amount;
    }
    if (_amount != 0) {
      underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L601-605)
```text
    _onlyIdleCDO();
    if (_amount > 0) {
      underlyingToken.safeTransferFrom(msg.sender, address(this), _amount);
      _mint(msg.sender, _amount);
    }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L686-708)
```text
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
    defaultRecoveryEpoch = epochNumber;
    // A non-zero pending instant bucket means current-epoch instant receipts were not fully funded
    // and must be paid through the same recovery ratio as normal pending receipts.
    defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0;
    // Bring active CDO NAV to the same recovery ratio. IdleCDOEpochVariant then calls
    // _forceUpdateAccounting so tranche prices/virtualPrice expose the crystallized loss.
    uint256 activeFinalNAV = (activeBasis * recoveryPrice) / RECOVERY_FULL;
    defaultBBNav = defaultBBNav * recoveryPrice / RECOVERY_FULL;
    if (activeBalance > activeFinalNAV) {
      _burn(idleCDO, activeBalance - activeFinalNAV);
    } else if (activeFinalNAV > activeBalance) {
      _mint(idleCDO, activeFinalNAV - activeBalance);
    }
    if (_recoveredAmount != 0) {
      // Pull external recovery last: if the transfer fails, the whole finalization reverts.
      underlyingToken.safeTransferFrom(_recoverySource, address(this), _recoveredAmount);
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L912-917)
```text
  function _transferDefaultRecovery(address _user, uint256 _amount) internal {
    if (_amount == 0) return;
    // Every defaulted or post-default claim consumes the isolated recovery reserve.
    defaultRecoveryReserve -= _amount;
    underlyingToken.safeTransfer(_user, _amount);
  }
```
