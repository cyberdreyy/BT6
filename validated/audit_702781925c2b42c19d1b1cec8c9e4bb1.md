### Title
TVL limit allows a lender to monopolize capacity and temporarily deny all other deposits - ([File: contracts/GuardedLaunchUpgradable.sol](contracts/GuardedLaunchUpgradable.sol))

### Summary

`IdleCDOCreditVault` enforces a global guarded-launch TVL cap before minting either tranche. An unprivileged, KYC-approved lender can consume the remaining capacity with their own deposit, causing competing deposits to revert with `ContractLimitReached()`. In epoch-based deployments, the attacker’s funds are committed until withdrawal processing, but the attacker can still selectively block rivals during the buffer/subscription period. A direct underlying transfer can also keep `getContractValue()` at the configured cap because that value includes raw underlying held by the CDO.

### Finding Description

`_guarded` rejects a deposit whenever the current contract value plus the new amount exceeds the configured nonzero `limit`:

```solidity
if (getContractValue() + _amount > _limit) revert ContractLimitReached();
```

Both `depositAA` and `depositBB` route to `_deposit`, which invokes `_guarded(_amount)` before accounting updates, transferring funds, minting shares, or depositing into the strategy:

```solidity
_guarded(_amount);
_updateAccounting();
_transferUnderlyingsFrom(msg.sender, address(this), _amount);
_minted = _mintSharesAtCurrPrice(...);
```

For the credit-vault implementation, `getContractValue()` returns:

```solidity
strategyTokenBalance + underlyingTokenBalance - unclaimedFees
```

Therefore:

1. Assume `limit = 10_000_000 USDC`.
2. Existing TVL is `9_000_000 USDC`.
3. A victim attempts to deposit `500_000 USDC`.
4. An attacker front-runs the transaction and deposits `1_000_000 USDC`.
5. TVL becomes the configured `10_000_000 USDC` cap.
6. The victim’s `depositAA(500_000)` or `depositBB(500_000)` reverts because `10_000_000 + 500_000 > limit`.
7. The attacker retains their tranche position and can later exit through the normal epoch withdrawal flow.

For prefunded queues, `requestDeposit` calls `checkPrefunding`, which calls the same `_guarded` check before accepting AA deposits for the next epoch. An attacker can therefore consume all remaining next-epoch capacity through the queue and cause subsequent queue requests to revert.

### Impact Explanation

The issue causes temporary denial of the deposit surface, not loss of already-deposited principal:

- If remaining capacity is `R`, an attacker deposit of `R` blocks every positive subsequent deposit.
- For example, with a `10_000_000 USDC` limit and `9_000_000 USDC` current TVL, `1_000_000 USDC` of attacker capital can block an arbitrary number of additional deposits until the attacker withdraws or the owner changes the cap.
- In an epoch credit vault, this can prevent competing lenders from obtaining exposure for that epoch. The quantified impact is the entire rejected deposit amount, capped by the victim’s intended subscription; economically, the victim loses the expected epoch return on that rejected amount.
- If raw underlying donations are included in `getContractValue()` before a skim occurs, an attacker can instead burn/donate `limit - getContractValue()` underlying directly to the CDO and keep the cap saturated without receiving tranches. This is more expensive but does not require deposit minting.

The broken fairness invariant is that one lender’s utilization of the global cap can arbitrarily exclude all other eligible lenders. The existing KYC checks do not prevent this because a KYC-passing lender is within the permitted attacker model.

### Likelihood Explanation

Likelihood depends on the configured `limit`:

- `limit == 0` disables the check, so the attack is unavailable.
- A finite production limit intended for guarded launch creates a race for capacity by design.
- An attacker needs enough capital to consume the remaining allocation. This can be substantially less than the total cap when TVL is already near the limit.
- The attacker must remain exposed until the normal withdrawal mechanism becomes available in epoch-based vaults, so the attack is not necessarily free or flashloanable. However, temporary blocking remains practical when controlling epoch participation has enough value.

Because the cap is an intentional guarded-launch control, the severity is primarily market/fairness denial rather than insolvency or theft.

### Recommendation

Avoid enforcing the TVL cap as a first-come, first-served global limit when fair access is required. Prefer one of:

- Use `limit = 0` once unrestricted operation is intended.
- Enforce per-wallet or per-epoch allocation quotas in addition to, or instead of, only a global cap.
- Make queued allocations explicit and settle oversubscribed epochs pro rata rather than reverting later requests.
- Exclude unsolicited raw underlying donations from the value used by `_guarded`, or skim them before evaluating remaining capacity.
- If a global cap remains necessary, document that it intentionally creates competitive capacity and monitor for abusive saturation.

### Proof of Concept

The following Foundry test demonstrates the deposit race. Exact helper names can be adapted to the repository’s credit-vault fixture.

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";

contract GuardedLaunchDepositDenialTest is Test {
    function testAttackerConsumesRemainingTvlLimit() external {
        uint256 cap = 10_000_000e6;
        uint256 existing = 9_000_000e6;
        uint256 victimAmount = 500_000e6;

        // Honest owner configures a finite guarded-launch cap.
        vm.prank(owner);
        cdo._setLimit(cap);

        // Existing honest lender brings TVL to `existing`.
        depositAA(existingLender, existing);
        assertEq(cdo.getContractValue(), existing);

        uint256 remaining = cap - existing;
        deal(underlying, attacker, remaining);
        deal(underlying, victim, victimAmount);

        // KYC/allowlist checks are assumed to pass for both ordinary lenders.
        allowWallet(attacker);
        allowWallet(victim);

        // Attacker front-runs the victim and consumes all remaining capacity.
        vm.startPrank(attacker);
        IERC20(underlying).approve(address(cdo), remaining);
        cdo.depositAA(remaining);
        vm.stopPrank();

        assertEq(cdo.getContractValue(), cap);

        // Every positive competing deposit now exceeds the global cap.
        vm.startPrank(victim);
        IERC20(underlying).approve(address(cdo), victimAmount);
        vm.expectRevert(ContractLimitReached.selector);
        cdo.depositAA(victimAmount);
        vm.stopPrank();

        assertEq(IERC20(underlying).balanceOf(victim), victimAmount);
    }
}
```

For a prefunded epoch deployment, the equivalent sequence is:

```solidity
vm.prank(owner);
cdo._setLimit(cap);

vm.prank(attacker);
queue.requestDeposit(cap - cdo.getContractValue());

vm.prank(victim);
vm.expectRevert(ContractLimitReached.selector);
queue.requestDeposit(victimAmount);
```

This reproduces the same access-control failure through the queue’s `checkPrefunding -> _guarded` path.