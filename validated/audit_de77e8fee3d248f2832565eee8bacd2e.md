### Title
Unchecked ERC20 `transfer` in `MerkleClaim.claim` burns the claim while silently failing to pay out - (File: contracts/MerkleClaim.sol)

### Summary
`MerkleClaim.claim` sets `hasClaimed[to] = true` before performing a raw `IERC20Upgradeable(token).transfer(to, amount)` whose boolean return value is never checked (contracts/MerkleClaim.sol:63-66). For ERC20 tokens that return `false` on failure instead of reverting, the transfer silently fails while the user's claim is permanently marked as consumed.

### Finding Description
The external report describes the classic "unchecked ERC20 transfer return value" bug class: a contract calls `transfer`/`transferFrom`, ignores the boolean result, and a failed transfer goes unnoticed.

In the idle-tranches codebase, the credit-vault periphery contract `MerkleClaim` is a cloneable token-distribution contract used to pay out tokens to merkle-proven recipients. Its `claim` function:

```solidity
// contracts/MerkleClaim.sol
hasClaimed[to] = true;                          // line 63 — state updated first
IERC20Upgradeable(token).transfer(to, amount);  // line 66 — return value ignored
```

The same pattern exists in `sweep` (line 74), but that is restricted to `TL_MULTISIG` (honest), so the exploitable surface is `claim`, which is callable by anyone for any proven `(to, amount)` leaf.

Many ERC20s (e.g., tokens that do not revert on failure such as certain older or non-standard implementations of the underlying/reward asset being distributed) return `false` rather than reverting on insufficient balance or blacklisted recipients. Because the return value is discarded, execution continues normally and `hasClaimed[to]` remains set. The claimee can never retry — a second call reverts with `AlreadyClaimed()` — and the tokens remain locked in the contract until `TL_MULTISIG` sweeps them after 60 days (line 72).

### Impact Explanation
Permanent freezing/loss of unclaimed yield: a legitimate claimee's merkle allocation is consumed (`hasClaimed` set) while zero tokens are delivered. The tokens then sit in the contract and are eventually swept by the multisig, so the claimee's distribution is irrecoverable. This breaks the "one receipt one payout" invariant in the worst way — the receipt is burned with no payout at all. The loss is quantified as the full `amount` proven in the merkle leaf for every affected claimee when the distributed token silently fails.

### Likelihood Explanation
Likelihood is moderate and conditional: it requires the distributed `token` to be a non-reverting, false-returning ERC20 (or to hit a failure path such as a blocked recipient), and `claim` is permissionless so any EOA can trigger it for any valid leaf — including front-running a legitimate claim to trigger the burned-claim state for the victim's leaf. No privileged role is needed as attacker; the honest multisig's later `sweep` merely finalizes the loss. Medium severity, matching the original report's classification.

### Recommendation
Use `SafeERC20Upgradeable.safeTransfer` for the payout (consistent with the rest of the codebase, e.g. `IdleCreditVault`, `DefaultDistributor`, `IdleCDOEpochQueue` all use `safeTransfer`/`safeTransferFrom`), or check the return value and revert on `false`. Alternatively, set `hasClaimed` only after a verified successful transfer so a failed payout can be retried.

```solidity
import { SafeERC20Upgradeable } from "@openzeppelin/contracts-upgradeable/token/ERC20/utils/SafeERC20Upgradeable.sol";
...
SafeERC20Upgradeable.safeTransfer(IERC20Upgradeable(token), to, amount);
```

### Proof of Concept
Foundry PoC sketch (deploy `MerkleClaim` clone + a `MockFalseToken` that returns `false` on transfer for a flagged recipient):

```solidity
// SPDX-License-Identifier: AGPL-3.0-only
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import "../contracts/MerkleClaim.sol";
import "@openzeppelin/contracts/utils/cryptography/MerkleProof.sol";
import "@openzeppelin/contracts-upgradeable/token/ERC20/IERC20Upgradeable.sol";

contract FalseToken is IERC20Upgradeable {
    mapping(address => uint256) public override balanceOf;
    mapping(address => mapping(address => uint256)) public override allowance;
    uint256 public override totalSupply;
    function mint(address to, uint256 amt) external { balanceOf[to] += amt; totalSupply += amt; }
    function transfer(address to, uint256 amt) external override returns (bool) {
        // simulate silent failure: deduct nothing, return false
        return false;
    }
    function transferFrom(address f, address t, uint256 a) external override returns (bool) { return false; }
    function approve(address, uint256) external override returns (bool) { return true; }
}

contract MerkleClaimUncheckedTransferTest is Test {
    MerkleClaim claim;
    FalseToken token;
    address alice = address(0xA11CE);
    uint256 amount = 100e18;
    bytes32 root;

    function setUp() public {
        claim = new MerkleClaim();
        token = new FalseToken();
        // leaf = double keccak of (to, amount)
        bytes32 leaf = keccak256(bytes.concat(keccak256(abi.encode(alice, amount))));
        root = leaf; // single-leaf tree
        claim.initialize(root, address(token));
        token.mint(address(claim), amount); // contract funded
    }

    function testClaimBurnedOnSilentFailure() public {
        bytes32[] memory proof = new bytes32[](0);
        // attacker/keeper calls claim for alice's leaf — succeeds silently
        claim.claim(alice, amount, proof);
        // alice got nothing
        assertEq(token.balanceOf(alice), 0);
        // claim is consumed forever
        vm.expectRevert(MerkleClaim.AlreadyClaimed.selector);
        claim.claim(alice, amount, proof);
        // funds are stuck in the claim contract, sweepable only by TL_MULTISIG
        assertEq(token.balanceOf(address(claim)), amount);
    }
}
```

Note: I verified this pattern directly in `MerkleClaim.sol`. `contracts/strategies/idle/IdleCreditVault.sol` also contains 4 raw `transfer` matches that I was unable to inspect within the iteration limit; if any of those are unchecked ERC20 transfers on the underlying asset, they would constitute an additional instance of the same bug class and should be reviewed.