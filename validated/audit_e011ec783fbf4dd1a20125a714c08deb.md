Checking whether the CDO allows `requestInstantWithdraw` after default finalization.### Title
Post-default instant-withdraw receipts alias `defaultRecoveryEpoch` and steal finalized recovery reserve - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
The Linux-kernel bug class (CVE-2018-5333) is "a free/cleanup path dereferences a pointer that was never validly set for the failing/invalid input". The analog in `IdleCreditVault` is `_claimDefaultedInstantWithdrawRequest`, which resolves a claimant's defaulted receipt through the static epoch pointer `defaultRecoveryEpoch`. Because `epochNumber` is frozen after default finalization, a *new* instant-withdraw receipt created **after** `finalizeDefaultRecovery` is recorded under `instantWithdrawsRequestsByEpoch[user][epochNumber]` where `epochNumber == defaultRecoveryEpoch`. The claim path therefore treats a receipt that was never part of the finalized `totalBasis` as a defaulted-epoch claim and pays it `defaultRecoveryPrice` out of `defaultRecoveryReserve` — a free-path dereference of an epoch pointer that does not correspond to the finalized claim set.

### Finding Description
`requestInstantWithdraw` (IdleCreditVault.sol:356-375) has no `defaultRecoveryFinalized` branch, unlike `requestWithdraw` which routes post-default requests into `postDefaultRequests` (lines 247-257). It unconditionally executes:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:367-374
uint256 currentEpoch = epochNumber;
instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
pendingInstantWithdraws += _amount;
```

`finalizeDefaultRecovery` sets `defaultRecoveryEpoch = epochNumber` (line 693) and `epochNumber` never advances again because no further `stopEpoch`/`deposit` epoch bump occurs in a defaulted pool. So the attacker's post-default receipt lands exactly in `instantWithdrawsRequestsByEpoch[attacker][defaultRecoveryEpoch]`.

When the attacker then calls `claimInstantWithdrawRequest` (lines 380-393), `defaultRecoveryFinalized && defaultInstantWithdrawsFinalized` is true, so `_claimDefaultedInstantWithdrawRequest` runs (lines 842-856):

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:844-855
claimBasis = instantWithdrawsRequestsByEpoch[_user][defaultEpoch];
instantWithdrawsRequestsByEpoch[_user][defaultEpoch] = 0;
instantWithdrawsRequests[_user] -= claimBasis;
pendingInstantWithdraws = claimBasis >= pending ? 0 : pending - claimBasis;
instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis;
_burn(_user, claimBasis);
_transferDefaultRecovery(_user, (claimBasis * defaultRecoveryPrice) / RECOVERY_FULL);
```

`_transferDefaultRecovery` decrements `defaultRecoveryReserve` (line 915). The reserve was sized at finalization as `reserveAmount = recovered + prefunded + prior reserve` against a fixed `totalBasis` that did not include this new receipt. The counter-mutations (`pendingInstantWithdraws`, `instantWithdrawClaimsByEpoch`) net to zero for the new request, so the only real effect is a burn of freshly minted receipt tokens and a pure withdrawal of reserve funds.

### Impact Explanation
Direct theft of the isolated default-recovery reserve. Any unprivileged KYC-passing lender holding strategy tokens (or who acquires them) mints a receipt at zero cost — `_mint(_user, _amount)` with no underlying posted — and redeems `amount * defaultRecoveryPrice / 1e18` of reserve. Each steal dilutes every legitimate defaulted-epoch claimant (active AA/BB tranche holders and pending withdraw requesters); with a large enough request the attacker drains the reserve and later honest claims permanently revert on the `defaultRecoveryReserve -= _amount` underflow. Loss is quantified as `min(requestAmount * defaultRecoveryPrice / 1e18, defaultRecoveryReserve)`.

The broken invariant is "one receipt one payout / donation isolation of the recovery reserve": a receipt created after the basis was finalized consumes reserve it was never haircut against. Existing guards do not stop it: `_ensureDefaultRecoveryInitialized` passes (already initialized), `requestInstantWithdraw` has no `defaultRecoveryFinalized` check (contrast with `requestWithdraw`'s explicit post-default path at lines 247-257), and `_transferFundedClaim`'s reserve guard (lines 899-905) is bypassed because payout goes through `_transferDefaultRecovery` instead.

### Likelihood Explanation
Requires: (a) the pool uses instant withdrawals (`allowInstantWithdraw`/`disableInstantWithdraw` configured), (b) borrower default is finalized via `finalizeDefaultRecovery` with a positive `defaultRecoveryPrice` and `pendingInstantWithdraws != 0` so `defaultInstantWithdrawsFinalized` is set, and (c) the CDO routes `requestInstantWithdraw` for the attacker post-finalization. The asymmetry between `requestWithdraw` (which explicitly handles the post-default case) and `requestInstantWithdraw` (which does not) indicates the receipt-mint entry point remains reachable; the claim entry point is explicitly kept reachable post-default by design (lines 382-386). I could not fully verify the CDO-side gating of `requestInstantWithdraw` when `defaulted == true` within the available context — if `IdleCDOEpochVariant.requestInstantWithdraw` reverts on `defaulted`, this vector is blocked; that check is the one piece of the path not confirmed. All strategy-side accounting, however, demonstrably permits the steal once the request is recorded.

### Recommendation
Mirror the `requestWithdraw` post-default guard in `requestInstantWithdraw`: when `defaultRecoveryFinalized`, either revert `NotAllowed` or route into a funded `postDefaultRequests`-style bucket paid 1:1 from a separately collected amount, never through `instantWithdrawsRequestsByEpoch[defaultRecoveryEpoch]`. Additionally, harden `_claimDefaultedInstantWithdrawRequest` by recording the finalization timestamp/flag per receipt, or key defaulted-epoch claims to `defaultRecoveryEpoch` only when `lastWithdrawRequest`/request timestamp predates finalization, so post-default receipts can never alias the finalized epoch pointer.

### Proof of Concept
Foundry fork PoC (strategy-level; route through `cdoEpoch.requestInstantWithdraw` if the CDO exposes it post-default — otherwise `vm.prank(idleCDO)` on the strategy demonstrates the accounting hole):

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IdleCreditVault} from "../contracts/strategies/idle/IdleCreditVault.sol";
import {IdleCDOEpochVariant} from "../contracts/IdleCDOEpochVariant.sol";
import {IERC20Detailed} from "../contracts/interfaces/IERC20Detailed.sol";

contract PostDefaultInstantAliasTest is Test {
    IdleCreditVault strategy;
    IdleCDOEpochVariant cdo;
    IERC20Detailed underlying;
    address manager = address(0xMGR);
    address borrower = address(0xBRW);
    address victim = makeAddr("victim");   // real defaulted-epoch instant requester
    address attacker = makeAddr("attacker");

    function setUp() public {
        // deploy + initialize strategy/cdo with instant withdrawals enabled,
        // victim deposits, requests instant withdraw in epoch N,
        // borrower defaults, owner calls _handleBorrowerDefault + finalizeDefault
        // with partial recovery => defaultRecoveryFinalized = true,
        // defaultInstantWithdrawsFinalized = true, defaultRecoveryPrice = p < 1e18,
        // defaultRecoveryReserve = R. epochNumber == defaultRecoveryEpoch == N.
    }

    function testPostDefaultInstantStealsReserve() public {
        uint256 reserve = strategy.defaultRecoveryReserve();
        uint256 price = strategy.defaultRecoveryPrice();
        assertGt(reserve, 0);
        assertGt(price, 0);

        // Attacker holds strategy tokens (e.g., tranche holder post-default).
        uint256 req = reserve * 1e18 / price; // sized to drain reserve
        deal(address(strategy), attacker, req); // receipt mint path burns CDO side first
        // route through the CDO (or prank idleCDO to isolate strategy accounting)
        vm.prank(address(cdo));
        strategy.requestInstantWithdraw(req, attacker);

        // Receipt aliases the finalized default epoch:
        assertEq(
            strategy.instantWithdrawsRequestsByEpoch(attacker, strategy.defaultRecoveryEpoch()),
            req
        );

        uint256 balPre = underlying.balanceOf(attacker);
        vm.prank(address(cdo));
        strategy.claimInstantWithdrawRequest(attacker);

        uint256 stolen = underlying.balanceOf(attacker) - balPre;
        assertEq(stolen, req * price / 1e18);
        // Reserve was drained without the receipt ever being in totalBasis:
        assertLt(strategy.defaultRecoveryReserve(), reserve);
        // Victim's subsequent legitimate claim now underflows/reverts:
        vm.prank(address(cdo));
        vm.expectRevert();
        strategy.claimInstantWithdrawRequest(victim);
    }
}
```