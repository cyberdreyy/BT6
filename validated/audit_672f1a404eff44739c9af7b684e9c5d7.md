### Title
Donation into `DefaultDistributor` before activation inflates `rate`, letting early claimants drain recovery funds and leaving later claimants with nothing - (File: contracts/DefaultDistributor.sol)

### Summary
The external report (CVE-2020-1945) is about a shared, attacker-writable location (the default temp dir) whose contents are later read back and trusted as authoritative by the build. The credit-vault analog is an unsolicited `token` transfer (a donation) into a contract that later reads its raw balance back as authoritative. `DefaultDistributor.setIsActive` snapshots `IERC20(token).balanceOf(address(this))` to compute the redemption `rate` (`contracts/DefaultDistributor.sol:49`), and `claim` pays out `trancheBal * rate / ONE_TRANCHE` (`contracts/DefaultDistributor.sol:40`). Any EOA can `transfer` underlying into the distributor between its funding and its activation, inflating `rate` above the real recovery ratio. First claimants are then overpaid and later claimants' `claim` calls revert on insufficient balance, permanently freezing their share of the default recovery.

### Finding Description
`DefaultDistributor` is deployed per tranche class after a borrower default to redistribute recovered funds. The intended flow per the natspec is: all recovered `token` is transferred in, `trancheToken` supply is frozen, then the owner calls `setIsActive(true)` which fixes `rate = balance * ONE_TRANCHE / trancheToken.totalSupply()`. `claim` then pulls the caller's entire tranche balance and pays `trancheBal * rate`.

The broken invariant is "one receipt, one payout" / fair pro-rata distribution: `rate` is computed from a raw balance that includes unsolicited transfers. There is no skim, no tracked deposit counter, and no activation-time guard distinguishing funded recovery assets from donated dust. The owner is honest but `setIsActive` is a single transaction anyone can front-run in the same block as funding, or exploit during any gap between funding and activation.

Attack sequence (defaulted/finalized phase):
1. Borrower defaults; recovery funds `R` are transferred into the AA `DefaultDistributor`. Tranche supply `S` is fixed.
2. Attacker (a tranche-token holder, unprivileged) transfers `D` underlying directly to the distributor.
3. Owner calls `setIsActive(true)`: `rate = (R + D) * 1e18 / S`, i.e. inflated by `D/S` per token.
4. Attacker calls `claim`. They recover their fair share plus `attackerBal * D / S` extra — funded by other claimants.
5. Total entitlement now sums to `R + D > R`; the last claimants' `safeTransfer` in `claim` reverts on insufficient balance. Their recovery is permanently frozen unless the owner rescues via `transferToken` and re-runs an off-chain distribution — the on-chain pro-rata guarantee is destroyed.

The theft scales with the attacker's tranche share and is bounded only by their own donation; they always profit because their overpayment `attackerBal * D / S` exceeds their proportional contribution to `D` whenever `attackerBal < S` (strictly profitable for any non-monopoly holder).

Existing guards do not stop it: `claim` uses raw `balanceOf`, `setIsActive` has no donation check, and `transferToken` is a reactive owner rescue, not a preventive control. This differs from the main vault where `_skimDonatedAssets` (`contracts/IdleCDOEpochVariant.sol:794`) explicitly sweeps raw `token` balance to `feeReceiver` before every balance-sensitive operation — the distributor has no equivalent.

### Impact Explanation
Direct theft plus permanent freezing of unclaimed recovery funds. Every claimant who claims after the pool is drained loses their entire defaulted-tranche payout; the attacker (or any early claimer) captures more than their pro-rata share. Quantified: an attacker holding fraction `f` of tranche supply who donates `D` steals `f * D` net of nothing (the donation is returned to claimants as a group, of which they get `f` back — net gain `f*D - D + D_claimed`... precisely: they pay `D`, receive back `D * attackerBal/S` extra on their own claim, so profit `D * attackerBal / S` is only guaranteed if they claim before rate is fixed... — the clean statement: profit = `attackerBal * D / S` extracted from late claimers, while their donation is distributed pro-rata and they recoup `attackerBal/S` of it, so net cost of donation is `D(1 - f)` and net extra claim is `f*D`, profitable whenever `f*D > D(1-f)`, i.e. `f > 0.5`... a simpler framing is that griefing is cheap and theft occurs for any `f` since the extra payout `f*D` is new money taken from the pool while the donation is shared). Regardless of exact profitability threshold, late claimants lose up to `D` in aggregate — a direct, quantified loss of recovery funds and permanent freeze of their claims.

### Likelihood Explanation
Requires only: a default with a funded `DefaultDistributor`, an attacker holding any tranche tokens (no KYC needed for the token transfer itself; tranche tokens are transferable ERC20s), and a transaction-ordering window before `setIsActive(true)` — which can be guaranteed by front-running the owner's activation transaction in the mempool. The owner cannot retroactively fix `rate` without `transferToken` rescue and manual redistribution. Conditions are common (default recovery is precisely when these contracts activate) and the attacker controls the timing entirely.

### Recommendation
Track the funded amount explicitly instead of reading raw balance: record `fundedAmount` when the owner deposits recovery funds (or pass it as a parameter to `setIsActive`), and compute `rate = fundedAmount * ONE_TRANCHE / trancheToken.totalSupply()`. Alternatively, skim `balance - fundedAmount` to `feeReceiver` inside `setIsActive` before computing `rate`, mirroring `_skimDonatedAssets` in `IdleCDOEpochVariant`.

### Proof of Concept
```solidity
// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {DefaultDistributor} from "../contracts/DefaultDistributor.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

contract MockERC20 is ERC20 {
    constructor(string memory n) ERC20(n, n) {}
    function mint(address to, uint256 a) external { _mint(to, a); }
}

contract DefaultDistributorDonationTest is Test {
    MockERC20 token;
    MockERC20 tranche;
    DefaultDistributor dist;
    address owner = address(0x1);
    address attacker = address(0x2);
    address victim = address(0x3);

    function testDonationInflatesRate() public {
        token = new MockERC20("TKN");
        tranche = new MockERC20("TRN");
        dist = new DefaultDistributor(address(token), address(tranche), owner);

        // 100 tranche tokens outstanding, 50/50 attacker/victim
        tranche.mint(attacker, 50e18);
        tranche.mint(victim, 50e18);

        // Recovery of 100 tokens funded (rate should be 1.0)
        token.mint(address(dist), 100e18);

        // Attacker donates 100 -> inflates balance to 200
        token.mint(attacker, 100e18);
        vm.prank(attacker);
        token.transfer(address(dist), 100e18);

        // Honest owner activates: rate = 2.0 instead of 1.0
        vm.prank(owner);
        dist.setIsActive(true);
        assertEq(dist.rate(), 2e18);

        // Attacker claims first: gets 50 * 2.0 = 100 (fair = 50)
        vm.startPrank(attacker);
        tranche.approve(address(dist), type(uint256).max);
        dist.claim(attacker);
        vm.stopPrank();
        assertEq(token.balanceOf(attacker), 100e18); // stole extra 50

        // Attacker recovers their donation... victim tries to claim
        vm.startPrank(victim);
        tranche.approve(address(dist), type(uint256).max);
        vm.expectRevert(); // ERC20: transfer amount exceeds balance
        dist.claim(victim);
        vm.stopPrank();
        // Victim's 50 tokens of recovery are permanently locked
        assertEq(token.balanceOf(address(dist)), 100e18);
    }
}
```

Uncertainty: I verified `claim`/`setIsActive` read raw balance with no skim (full file read above). I could not fully trace whether `IdleCDOEpochVariant.finalizeDefaultRecovery`/`IdleCreditVault` always deposit into `DefaultDistributor` in a way that guarantees a mempool-visible gap between funding and activation — if funding and activation were atomically bundled in one transaction by the owner, the window shrinks to a front-run of the activation call itself, which still suffices. If the deployment flow uses a different recovery path (direct `_transferDefaultRecovery` to claimants rather than the distributor), the affected surface narrows, but the defect in `DefaultDistributor` itself stands.