### Title
ProgrammableBorrower deposits into the external ERC4626 vault with no min-shares/slippage check, exposing pooled lender funds to a share-inflation (donation) attack - (File: contracts/strategies/idle/ProgrammableBorrower.sol)

### Summary
The Castor XXE class bug — unconditionally resolving an externally controlled reference — maps here to `ProgrammableBorrower` blindly trusting the external ERC4626 vault's exchange rate. Every deposit into `vault` (`_depositToVault`, called from `onStartEpoch` and `_repay`) and every withdrawal (`vault.withdraw` in `onStopEpoch` and `_borrow`) is executed with zero output validation. Because `convertToAssets`/share minting are controlled by an unprivileged external vault user, an attacker can manipulate the rate the pool settles at.

### Finding Description
`_depositToVault` calls `vault.deposit(_assetAmount, address(this))` and accepts whatever `shares` come back, with no `minShares` parameter and no comparison against `convertToShares(_assetAmount)`:

- contracts/strategies/idle/ProgrammableBorrower.sol `L378-385` (`_depositToVault`)
- Called from `onStartEpoch` (`L216`: `_depositToVault(underlyingToken.balanceOf(address(this)), 0)` — the full idle pool balance is re-parked at each epoch start) and from `_repay` (`L499`, `L527` — repayments are re-deployed while an epoch is active).
- `onStopEpoch` `L246` calls `vault.withdraw(shortfall, …)` and `_borrow` `L452` does the same, again with no min-assets bound.

When the configured vault is empty or near-empty (freshly set via `initialize`/`setVault`, or fully drained after an `emergencyExitVault`/close-pool unwind and a subsequent re-deployment), an unprivileged vault user can execute the classic ERC4626 inflation attack: deposit 1 wei to mint 1 share, then donate underlying directly to the vault to inflate `convertToAssets(1 share)` to `W`. The next `vault.deposit(D, …)` by `ProgrammableBorrower` mints `floor(D / (W + 1))` shares — 0 shares whenever the attacker picks `W ≥ D`. The attacker then redeems their single share for `W + 1 + D`, absorbing the pool's entire deposit.

Accounting does not save the position: `epochDepositedToVault`/`epochWithdrawnFromVault` are tracked in asset units (`L382`, `L248`, `L454`), while `_currentVaultAssets()` re-reads `vault.convertToAssets(balance)` (`L546-549`). A 0-share mint is invisible to `_vaultNetInterest` except as a vault loss equal to the stolen amount, so `totalInterestDueNow` silently reports the loss and the pool realizes it at the next `stopEpoch`.

### Impact Explanation
Direct theft of pooled lender principal. The whole idle underlying balance of the facility is deposited at every `onStartEpoch` (`L216`), so on the first epoch after a vault is configured empty, an attacker can capture up to ~100% of that deposit (bounded by the donation `W` they front; stealing fraction `f` of `D` costs roughly `f·D` in donation, and the standard rounding variant steals the entire deposit for `W ≈ D`). The stolen assets are permanently removed from pool backing: `vaultSharesBalance()` is ~0 while the deposited principal was already moved off the contract, so `getContractValue`/tranche prices reflect the loss at the next `_updateAccounting`. No privileged action is required — only EOA transactions against the public ERC4626 vault.

### Likelihood Explanation
Requires a deposit event while the vault's share supply or asset base is small relative to the deposit. This is realistic: `setVault` (`L158-170`) only requires `vault.balanceOf(this) == 0` and matching asset, so the owner/manager can legitimately point the borrower at a fresh vault; `initialize` accepts any ERC4626; and `onStartEpoch` re-deposits the entire idle balance each epoch, so the first epoch after configuration (or after a full unwind via `emergencyExitVault`/close-pool) is a deterministic, publicly visible target in the mempool. The attack capital (`W ≈ D`) limits profitability on very large deposits, but partial capture of smaller `repay`-driven redeposits (`_repay` → `_depositToVault`, `L524-527`) is cheap whenever the vault is thin. Existing guards do not help: `nonReentrant` doesn't apply cross-transaction, `InvalidAmount`/liquidity checks never inspect minted shares, and IdleCDO's `_skimDonatedAssets` only covers direct underlying donations to the CDO, not vault-side donations.

### Recommendation
Validate vault exchange output on every interaction in `ProgrammableBorrower.sol`:
- In `_depositToVault`, enforce a minimum: `require(shares >= vault.convertToShares(_assetAmount) * MIN_BPS / 10_000)` or `require(shares > 0)` plus a share-price sanity bound against a stored initial rate.
- In `onStopEpoch`/`_borrow`, bound shares burned vs. `convertToShares(shortfall)`.
- Optionally require a minimum vault `totalSupply`/`totalAssets` in `initialize`/`setVault` to avoid the empty-vault inflation window, and/or mint a dead-share seed deposit at initialization.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {ProgrammableBorrower} from "../contracts/strategies/idle/ProgrammableBorrower.sol";
// reuse MockStopEpochLiquidityVault from test/foundry/ProgrammableBorrowerCreditVault.t.sol

contract VaultInflationPoC is Test {
    // attacker = unprivileged user of the borrower's ERC4626 vault
    function test_firstDepositorInflationStealsEpochDeposit() external {
        // Setup: ProgrammableBorrower initialized with a FRESH, EMPTY ERC4626 vault
        // (initialize() only requires vault.asset() == underlying).
        // IdleCDO.startEpoch() -> onStartEpoch() -> _depositToVault(idleBalance, 0)
        uint256 idleBalance = 1_000_000e6;

        // 1) Attacker front-runs onStartEpoch: mint 1 share, then donate W.
        uint256 W = idleBalance;                       // donation >= deposit
        vault.deposit(1, attacker);                    // 1 share
        underlying.mint(attacker, W);
        vm.prank(attacker);
        underlying.transfer(address(vault), W);        // convertToAssets(1) = W+1

        // 2) Honest flow: manager calls startEpoch -> CDO calls onStartEpoch ->
        //    _depositToVault(idleBalance, 0) mints floor(idleBalance / (W+1)) = 0 shares.
        vm.prank(idleCDO);
        programmableBorrower.onStartEpoch(0);
        assertEq(vault.balanceOf(address(programmableBorrower)), 0);

        // 3) Attacker redeems 1 share for W + 1 + idleBalance -> profit = idleBalance.
        vm.prank(attacker);
        uint256 stolen = vault.redeem(1, attacker, attacker);
        assertEq(stolen, W + 1 + idleBalance);

        // 4) Pool loss surfaces at stopEpoch: _vaultNetInterest reports loss ≈ idleBalance,
        //    totalInterestDueNow() = 0 and tranche holders absorb the principal loss.
        assertEq(programmableBorrower.vaultLoss(), idleBalance);
    }
}
```
Full Foundry context (MockStopEpochLiquidityVault, wiring of `IdleCDOEpochVariant` + `ProgrammableBorrower` via `TransparentUpgradeableProxy`, `underlying` mock) is already in `test/foundry/ProgrammableBorrowerCreditVault.t.sol`; the PoC only adds the attacker front-run around the existing `cdoEpoch.startEpoch()` call.