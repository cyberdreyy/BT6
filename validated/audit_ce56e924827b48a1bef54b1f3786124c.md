### Title
Unprivileged crystallization of stray underlying donations into tranche NAV before `_skimDonatedAssets` runs - (File: contracts/IdleCDOCreditVault.sol)

### Summary
The credit vault counts the raw underlying balance held by the CDO in `getContractValue()` (`strategyToken + token - unclaimedFees`), while the only mechanism that removes unsolicited donations (`_skimDonatedAssets`) is invoked exclusively inside privileged flows (`startEpoch`, `_stopEpoch`, `updateAccounting`, `finalizeDefault`, `depositDuringEpoch`). Any unprivileged `depositAA`/`depositBB` (or other non-privileged accounting touch) calls `_updateAccounting()`, which crystallizes the entire donated balance as a gain into `lastNAVAA`/`lastNAVBB` and tranche prices. Since the CDO does not track who sent the tokens, the donation is permanently redistributed pro-rata to current tranche holders instead of being returned or skimmed — the direct analog of untracked assets sent to the NToken contract being claimable by anyone.

### Finding Description
`IdleCDOCreditVault.getContractValue()` sums `_contractTokenBalance(strategyToken) + _contractTokenBalance(token) - unclaimedFees`, so any raw `token` sitting on the CDO is part of NAV (`contracts/IdleCDOCreditVault.sol:125-128`). `_managedContractValue()` deliberately excludes raw underlyings and is used for `virtualPrice`, but `_updateAccounting()` uses `getContractValue()` directly (`contracts/IdleCDOCreditVault.sol:222-234`):

```solidity
uint256 nav = getContractValue();
if (nav > _lastNAV) {
  unclaimedFees += (nav - _lastNAV) * fee / FULL_ALLOC;
}
(uint256 _priceAA, int256 _totalAAGain) = _virtualPriceAux(...);
lastNAVAA = uint256(int256(_lastNAVAA) + _totalAAGain);
lastNAVBB = uint256(int256(_lastNAVBB) + _totalBBGain);
```

`_deposit` (callable by any KYC-passed user via `depositAA`, or anyone when BB deposits are enabled) calls `_updateAccounting()` *before* pulling funds and minting at the new price, and never skims (`contracts/IdleCDOCreditVault.sol:191-212`). The intended mitigation `_skimDonatedAssets()` is only reached inside `updateAccounting` (owner/guardian), `startEpoch`, `_stopEpoch`, `finalizeDefault`, and `depositDuringEpoch` (`contracts/IdleCDOEpochVariant.sol:157-160, 241-242, 354-355, 196-197`).

Sequence during the buffer phase:

1. Alice (or any unprivileged sender) mistakenly transfers `D` units of `token` directly to the CDO address.
2. Attacker, who holds a large share (or all) of a tranche — e.g., sole BB holder — calls `depositAA(1 wei)` or any tiny deposit.
3. `_updateAccounting()` computes `nav` including `D`, takes `fee` of `D` into `unclaimedFees`, and splits the remainder `D - fee` into `lastNAVAA`/`lastNAVBB` per `trancheAPRSplitRatio`, permanently raising `priceAA`/`priceBB`.
4. Alice's tokens are now baked into tranche NAV. When the manager later calls `startEpoch`/`stopEpoch`, `_skimDonatedAssets` finds nothing (the tokens' *value* is already owed to tranche holders, so the physical `D` is either skimmed to `feeReceiver` while NAV stays inflated, or consumed paying inflated claims — either way the pool is worse off or Alice's funds are gone).
5. Attacker redeems at the inflated price via `requestWithdraw`/claims and extracts up to `(D * (1 - fee)) * attackerShare` of value.

The broken invariant is donation isolation: raw untracked transfers must never enter tranche NAV. Existing guards do not stop this — `_skimDonatedAssets` runs only on privileged calls, `_managedContractValue` only protects `virtualPrice`/previews, and there is no owner-tracking for raw `token` transfers (same root cause as the reported NToken bug).

### Impact Explanation
Theft of mis-sent/donated underlying: any tokens transferred directly to the CDO by an unaware user can be captured by an unprivileged attacker pro-rata through tranche price inflation (up to ~`D * (1 - fee)` for a dominant holder). If skim happens after crystallization, the inflated `lastNAV` overstates obligations versus actual backing, producing insolvency that is socialized across withdrawers/claimants — loss is bounded by the size of the stray transfer, which is unbounded in principle.

### Likelihood Explanation
Medium. It requires a user to send `token` directly to the CDO contract (a realistic mistake — users interact with this address for deposits, and ERC20s carry no transfer hook to reject it). Once tokens sit on the contract, the attacker only needs to be a tranche holder or KYC'd depositor and front-run the next privileged call with a dust deposit; there is no time-based or access barrier.

### Recommendation
- Skim donations in `_deposit`/`_updateAccounting` itself (i.e., call `_skimDonatedAssets()` at the top of `_updateAccounting` or compute `nav` from `_managedContractValue()` plus explicitly tracked pending buffers rather than raw `_contractTokenBalance(token)`).
- Alternatively, subtract untracked raw underlying from `getContractValue()` the same way `_managedContractValue()` does, so no code path can ever crystallize donations into `lastNAV`.
- Optionally document that tokens sent directly to the CDO are recoverable only via `feeReceiver` skim, or add a rescue function for verified original senders.

### Proof of Concept
A Foundry fork test on an `IdleCDOEpochVariant`/`IdleCDOCreditVault` deployment:

```solidity
function testDonationCrystallizedIntoNAV() external {
    uint256 amount = 10_000 * ONE_SCALE;
    uint256 donation = 1_000 * ONE_SCALE;

    // attacker is sole/predominant BB holder
    deal(defaultUnderlying, attacker, amount);
    vm.startPrank(attacker);
    underlying.approve(address(cdoEpoch), amount);
    cdoEpoch.depositBB(amount);          // or attacker is already a holder
    vm.stopPrank();

    uint256 navBBPre = cdoEpoch.lastNAVBB();

    // Alice mistakenly sends tokens directly to the CDO
    deal(defaultUnderlying, alice, donation);
    vm.prank(alice);
    underlying.transfer(address(cdoEpoch), donation);

    // Unprivileged dust deposit crystallizes the donation into NAV
    deal(defaultUnderlying, attacker, 1);
    vm.startPrank(attacker);
    underlying.approve(address(cdoEpoch), 1);
    cdoEpoch.depositBB(1);               // _updateAccounting runs, no skim
    vm.stopPrank();

    uint256 feeAmt = donation * cdoEpoch.fee() / cdoEpoch.FULL_ALLOC();
    assertGt(cdoEpoch.lastNAVAA() + cdoEpoch.lastNAVBB(),
             navBBPre + cdoEpoch.lastNAVAA() + donation - feeAmt - 1,
             "donation baked into tranche NAV");

    // Attacker requests withdraw at inflated price and captures Alice's funds pro-rata
    uint256 claimed = cdoEpoch.requestWithdraw(0, address(BBtranche));
    assertGt(claimed, amount, "attacker redeemed donation value");
}
```

Key assertion to verify in the PoC: `lastNAVAA + lastNAVBB` increases by ≈ `donation - fee` after a dust `depositBB`, while `underlying.balanceOf(feeReceiver)` only increases if/when a later privileged call skims the now-double-counted physical tokens — demonstrating either pro-rata theft or resulting NAV overstatement.

Note: I could not fully confirm whether `requestWithdraw`/`claimWithdrawRequest` payout amounts derive from `getContractValue()` or `_managedContractValue()` in every path; if all payout paths use `_managedContractValue`, the direct theft leg is reduced, but the NAV-overstatement/insolvency leg (inflated `lastNAV` vs. skimmed backing) still stands.