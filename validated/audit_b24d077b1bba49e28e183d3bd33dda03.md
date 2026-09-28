### Title
First-depositor donation attack inflates tranche price and steals subsequent depositors' funds - (File: contracts/IdleCDO.sol)

### Summary
`IdleCDO._deposit` mints tranche tokens at `_tranchePrice(_tranche)`, which is derived from `lastNAVXX / trancheSupply`. Because `getContractValue()` counts the contract's raw `token` balance, an attacker who is the first (or only) holder of a tranche can donate underlying directly to the contract, let `_updateAccounting()` book the donation as gain to their tranche, and thereby inflate the tranche price. A subsequent depositor is minted near-zero shares at the inflated price while the attacker redeems almost the entire NAV — the same flash-loan-funded "deposit at manipulated price / drain the pool" pattern as the Jimbo `shift()` exploit.

### Finding Description
- `getContractValue()` includes `_contractTokenBalance(token)`, i.e. any underlying pushed to the contract without a deposit is counted in NAV (contracts/IdleCDO.sol:181-187).
- `_deposit` calls `_updateAccounting()` *before* minting, so a donation made between two transactions is first booked as a tranche gain (contracts/IdleCDO.sol:234-255).
- For a tranche whose whole NAV is the attacker's, `_virtualPriceAux` assigns the full `totalGain` to that tranche when the opposite tranche has no supply (`_lastNAV == _lastTrancheNAV` path in IdleCDOCreditVault.sol:319-334, and the equivalent `otherSupply == 0` path in IdleCDO.sol `_virtualPriceAux`), so `priceAA = (lastNAVAA + donation) * ONE_TRANCHE_TOKEN / supply` (contracts/IdleCDO.sol:428-429).
- `_mintSharesAtCurrPrice` then mints `amount / inflatedPrice` shares to the victim (contracts/IdleCDO.sol:437-441); the attacker withdraws via `_withdraw`, redeeming `_amount * _tranchePrice` (contracts/IdleCDO.sol:493-506).
- The only related mitigations are `_updateCallerBlock`/`_checkSameBlock`, which prevent deposit→withdraw *within the same block by the same caller*, and `_guarded`, which enforces a deposit cap — neither blocks a multi-transaction inflation attack nor enforces a minimum first deposit / dead-share reserve.

Attack sequence (buffer/running phase, standard fixed-APR mode):
1. Attacker is first AA depositor: `depositAA(1 wei)` → receives ~1 wei of tranche tokens at `priceAA = oneToken`.
2. Attacker transfers `D` underlying directly to the IdleCDO contract (donation, possibly flash-loan funded).
3. Victim calls `depositAA(V)`. `_updateAccounting` books `D` as AA gain → `priceAA ≈ (1 + D) * 1e18`; victim mints `V / priceAA` shares ≈ dust.
4. Attacker calls `withdrawAA` and redeems `(1 + D + V_eff)` — i.e. recovers the donation plus nearly all of `V`.

### Impact Explanation
Direct theft of user funds: every underlying token deposited after the inflation is captured by the attacker (minus performance-fee dust). Loss ≈ the victim's deposit amount, bounded only by the `_guarded` deposit limit and the attacker's flash-loan capacity. Applies at vault launch and whenever a tranche's supply/NAV resets to zero (`trancheSupply == 0 → price = oneToken`), so it is repeatable.

### Likelihood Explanation
Medium. Requires the attacker to be the sole holder of a tranche (first depositor, or after all other holders exit) so the donation gain is not shared with the opposite tranche. This is fully achievable by an unprivileged EOA around a vault (re)deployment or after a mass redemption, and flash loans remove the capital requirement for `D`. No privileged action is needed. Uncertainty: specific deployed variants or wrappers may add a minimum-deposit or skim guard I could not fully enumerate; the core `IdleCDO` accounting path shown has none.

### Recommendation
- On the first mint of a tranche (or when `trancheSupply == 0`), mint a dead-share reserve (e.g. burn a fixed minimum amount of tranche tokens or seed `lastNAVXX` to an irrevocable address), as standard ERC-4626 inflation mitigations do.
- Alternatively, track NAV via internal accounting (`lastNAVXX` deltas) and exclude unsolicited `token` balance increases from `getContractValue`, or route unexpected balances to `unclaimedFees`/a skim function before they can move `priceAA`/`priceBB`.
- Enforce a minimum initial deposit and/or mint at `oneToken` only when supply is zero while capping shares to the post-`updateAccounting` NAV contribution.

### Proof of Concept
Foundry fork test (outline — `test/foundry/` style, mainnet fork):

```solidity
function testDonationInflationStealsDeposit() public {
    // fork mainnet, pick a live IdleCDO with underlying `token`
    IdleCDO cdo = IdleCDO(CDO_ADDR);
    IERC20Detailed tk = IERC20Detailed(cdo.token());
    address attacker = address(0xA11CE);
    address victim = address(0xB0B);

    uint256 D = 100_000e6;   // donation (can be flash-borrowed)
    uint256 V = 50_000e6;    // victim deposit
    deal(address(tk), attacker, D + 1);
    deal(address(tk), victim, V);

    // 1) attacker seeds tranche with dust
    vm.startPrank(attacker);
    tk.approve(address(cdo), 1);
    cdo.depositAA(1);                       // mint ~1 wei shares @ oneToken
    tk.transfer(address(cdo), D);           // donation inflates NAV
    vm.stopPrank();

    // 2) victim deposits
    vm.startPrank(victim);
    tk.approve(address(cdo), V);
    uint256 minted = cdo.depositAA(V);
    vm.stopPrank();

    // 3) attacker redeems nearly everything
    vm.prank(attacker);
    uint256 balBefore = tk.balanceOf(attacker);
    cdo.withdrawAA(0);
    uint256 profit = tk.balanceOf(attacker) - balBefore;

    assertLt(minted, V);                    // victim minted dust
    assertGt(profit, V + D - 1e6);          // attacker recovers D + ~V
}
```

Invariants broken: fair mint/burn (`_mintSharesAtCurrPrice` mints at a manipulated `priceAA`) and donation isolation (unsolicited `token` transfers flow straight into `getContractValue`/`_virtualPriceAux` instead of being skimmed or excluded).