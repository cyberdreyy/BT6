### Title
Unchecked `vault.deposit` return value lets a vault-share inflation attack steal parked lender funds — (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
`ProgrammableBorrower` deploys all idle lender capital into an external ERC4626 `vault` via `_depositToVault`, which calls `vault.deposit(_assetAmount, address(this))` and never validates the returned share count. If the vault's share price is inflated (e.g., a freshly configured vault that is pre-funded/donated, classic first-deposit inflation), the deposit mints 0 shares: the underlying is donated to existing vault shareholders while `_currentVaultAssets()` reads 0. The loss is then reported as `vaultLoss`/`totalInterestDueNow` to `IdleCDOEpochVariant` and socialized through the tranche waterfall, while the attacker redeems their single inflated share for nearly the whole deposit.

### Finding Description
`_depositToVault` is the single funnel for all cash deployment:

```solidity
// contracts/strategies/idle/ProgrammableBorrower.sol:378-385
function _depositToVault(uint256 _assetAmount, uint256 _principalAssets) internal {
  if (_assetAmount == 0) return;
  uint256 shares = vault.deposit(_assetAmount, address(this));
  if (epochAccountingActive && _principalAssets != 0) {
    epochDepositedToVault += _principalAssets;
  }
  emit DepositedIntoVault(_assetAmount, shares);
}
```

It is invoked from `onStartEpoch` (line 216, parks all idle balance into the vault), and from `_repay` (lines 499, 527, redeploys borrower repayments during an active epoch). In every path the returned `shares` value is only emitted in an event — no `shares > 0` check, no `convertToAssets` sanity check.

The accounting consequences:

- `_currentVaultAssets()` (line 546-549) returns `convertToAssets(vault.balanceOf(this))` → 0 when 0 shares were minted, so the deployed principal simply disappears from the strategy's books.
- `_vaultNetInterest()` (line 337-347) computes `earnedAssets - principalAssets`; the vanished deposit becomes `loss`.
- `vaultLoss()` / `totalInterestDueNow()` feed this to `IdleCDOEpochVariant.stopEpoch`, where the "loss" is priced into the epoch result.
- In `onStopEpoch`, `shortfall > _currentVaultAssets()` returns `true` without withdrawing (line 245), so IdleCDO's `transferFrom` fails → default path → loss hits junior/BB tranche first.

Attack prerequisites are all unprivileged: the attacker only needs to be a depositor in the configured ERC4626 vault (explicitly an allowed attacker class). Sequence: before the pool's first vault deposit (initial configuration, or after `setVault`/full withdrawal leaves `vault.balanceOf(this) == 0`), the attacker deposits 1 wei of underlying → receives 1 share → donates `D` underlying directly to the vault contract → share price becomes ~`D`. When `onStartEpoch` or a repay deposits `A < D` assets, `deposit` mints `floor(A * 1 / (D + 1)) = 0` shares. The attacker redeems their 1 share for `A + D`, netting `A` stolen from the pool.

### Impact Explanation
Direct theft plus forced insolvency accounting. The full deposited amount `A` (potentially the entire pooled lender balance deployed at epoch start, or borrower repayments during the epoch) is claimed by the attacker through the vault, while the CDO records it as a vault loss — junior tranche holders absorb the loss via the waterfall, and withdraw settlements are impaired. No privileged collusion is needed; honest owner/manager/queue calls are merely sequenced around.

### Likelihood Explanation
Requires the vault's share supply to be small at the moment of a strategy deposit — precisely the state at initialize (fresh vault), after `setVault` rotates to a new vault, or after a full withdrawal (`emergencyExitVault`/`onStopEpoch` draining to 0 shares). The attacker is any external vault depositor; a mempool observer or frontrunner can deposit-1-wei-then-donate in the same block as the epoch-start/repay deposit. Medium likelihood, high impact.

### Recommendation
Validate the minted shares (or their asset value) on every deposit path:

```solidity
uint256 shares = vault.deposit(_assetAmount, address(this));
if (shares == 0) revert InvalidAmount(); // or compare convertToAssets(shares) vs _assetAmount within tolerance
```

Additionally, consider requiring a minimum share supply / virtual-price sanity check on the vault at `initialize`/`setVault`, or seeding the vault position atomically with an initial deposit so the 0-share/inflation window never exists.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {ProgrammableBorrower} from "contracts/strategies/idle/ProgrammableBorrower.sol";
import {IERC4626} from "contracts/interfaces/IERC4626.sol";
import {IERC20Detailed} from "contracts/interfaces/IERC20Detailed.sol";
import {ERC4626, ERC20} from "solmate/tokens/ERC4626.sol"; // or OZ ERC4626 mock

contract DepositZeroSharesTest is Test {
    ERC20 asset = new ERC20("USDC","USDC",6);
    ERC4626 vault = new ERC4626(asset, "v","v");
    ProgrammableBorrower pb = /* deploy + initialize(vault, idleCDO, owner, manager, borrower, apr) */;
    address attacker = address(0xA);

    function test_ZeroShareDepositStealsPoolFunds() public {
        uint256 POOL_DEPOSIT = 100_000e6;
        uint256 DONATION = 1_000_000e6;

        // Attacker seeds and inflates the vault before pool's first deposit
        asset.mint(attacker, 1 + DONATION);
        vm.startPrank(attacker);
        asset.approve(address(vault), type(uint256).max);
        vault.deposit(1, attacker);                  // 1 share
        asset.transfer(address(vault), DONATION);    // inflate share price
        vm.stopPrank();

        // Honest epoch start: IdleCDO pushes idle cash -> _depositToVault
        asset.mint(address(pb), POOL_DEPOSIT);
        vm.prank(idleCDO);
        pb.onStartEpoch(0);

        assertEq(vault.balanceOf(address(pb)), 0);   // 0 shares minted, deposit uncaught
        assertEq(pb.totalUnderlying(), 0);           // books show the loss
        assertGt(pb.vaultLoss(), 0);                 // socialized via stopEpoch

        // Attacker redeems single share for donation + stolen pool deposit
        vm.prank(attacker);
        uint256 stolen = vault.redeem(1, attacker, attacker);
        assertGt(stolen, DONATION + POOL_DEPOSIT - 2); // net profit = POOL_DEPOSIT
    }
}
```

Run on a mainnet fork with the real configured ERC4626 vault to confirm 0-share minting and the downstream `vaultLoss`/`onStopEpoch` default path.