### Title
Unclaimed ERC20 marked as claimed before unchecked `transfer` return — a silent failed payout permanently blocks the claimee - (File: contracts/MerkleClaim.sol)

### Summary
`MerkleClaim.claim` sets `hasClaimed[to] = true` and then calls `IERC20Upgradeable(token).transfer(to, amount)` without checking the boolean return value. This is the same bug class as the external report: accounting state (`hasClaimed`) is committed before a token transfer whose success is never verified, and the contract proceeds as if the payout succeeded. If `transfer` returns `false` (tokens that signal failure via return value rather than reverting, e.g., insufficient balance on a non-reverting token), the claimee receives nothing but can never claim again, since the `AlreadyClaimed` revert at `MerkleClaim.sol:54` permanently locks them out.

### Finding Description
`claim` performs checks-effects-interactions in the wrong spirit:

- `MerkleClaim.sol:54` — `if (hasClaimed[to]) revert AlreadyClaimed();`
- `MerkleClaim.sol:58-60` — merkle proof verification.
- `MerkleClaim.sol:63` — `hasClaimed[to] = true;` (state committed)
- `MerkleClaim.sol:66` — `IERC20Upgradeable(token).transfer(to, amount);` (return value ignored)

For a compliant reverting ERC20 the failed transfer bubbles up and rolls back `hasClaimed`, so the bug is latent. But the `token` is deployer-configured (`initialize`, `MerkleClaim.sol:37-44`) and `IERC20Upgradeable.transfer` is invoked raw — not via `SafeERC20Upgradeable` — so any token whose `transfer` returns `false` instead of reverting (or a generic ERC20-compatible contract that swallows failures) leaves `hasClaimed` set with zero tokens delivered. The same unchecked pattern exists in `sweep` (`MerkleClaim.sol:74`).

The codebase itself treats this as a real hazard elsewhere: `IdleStrategy` routes all value-moving transfers through `SafeERC20Upgradeable.safeTransfer`/`safeTransferFrom` (`IdleStrategy.sol:81,143,159,211`) precisely to catch non-reverting false returns — `MerkleClaim` and `pullStkAAVE`/`rescue` (`IdleStrategy.sol:201`, `EthenaCooldownRequest.sol:29`) are the exceptions.

### Impact Explanation
A claimee whose distribution token returns `false` (e.g., contract balance < `amount`, or a non-reverting token implementation) loses their airdrop permanently from their perspective: `hasClaimed[to]` is already `true`, every retry reverts with `AlreadyClaimed`, and the tokens remain locked in the contract. Recovery depends entirely on the privileged `TL_MULTISIG` calling `sweep` after `deployTime + 60 days` (`MerkleClaim.sol:69-75`), so the funds are frozen for at least 60 days and are only recoverable through an honest-privileged-party action — i.e., this is theft-adjacent freezing of unclaimed yield borne by an unprivileged user, with the loss equal to their full merkle allocation.

### Likelihood Explanation
Low-to-medium. Exploitation does not require an attacker at all — it triggers passively the first time `transfer` returns `false`. However, it requires the configured `token` to be one that signals failure by return value rather than revert (the majority of mainstream tokens revert). There is no zero-amount guard needed here since `amount = 0` claims cause no fund loss, only a wasted `hasClaimed` flag. No existing guard (no `SafeERC20`, no success check, no post-transfer balance assertion) mitigates it.

### Recommendation
Check the transfer result or use `SafeERC20Upgradeable`:

```solidity
// contracts/MerkleClaim.sol
using SafeERC20Upgradeable for IERC20Upgradeable;
...
hasClaimed[to] = true;
IERC20Upgradeable(token).safeTransfer(to, amount);
```

Alternatively keep effects atomic: perform the transfer first and only set `hasClaimed` after confirming success (still protected against reentrancy by the claimed flag being set before external call — prefer `safeTransfer` + existing ordering).

### Proof of Concept
Foundry fork test sketch:

```solidity
// SPDX-License-Identifier: AGPL-3.0-only
pragma solidity 0.8.10;
import "forge-std/Test.sol";
import "../contracts/MerkleClaim.sol";

contract FalseOnFailToken {
    mapping(address => uint256) public balanceOf;
    function mint(address to, uint256 a) external { balanceOf[to] += a; }
    // ERC20-shaped but returns false instead of reverting on failure
    function transfer(address to, uint256 a) external returns (bool) {
        if (balanceOf[msg.sender] < a) return false;
        balanceOf[msg.sender] -= a; balanceOf[to] += a; return true;
    }
}

contract MerkleClaimUncheckedTransferTest is Test {
    MerkleClaim mc; FalseOnFailToken tok;
    address alice = address(0xA11CE);

    function setUp() public {
        mc = new MerkleClaim();
        tok = new FalseOnFailToken();
        // build single-leaf root for (alice, 1000)
        bytes32 leaf = keccak256(bytes.concat(keccak256(abi.encode(alice, uint256(1000)))));
        mc.initialize(leaf, address(tok));
        // NOTE: contract intentionally underfunded (or funded < 1000) -> transfer returns false
        tok.mint(address(mc), 500);
    }

    function test_claimMarkedButNothingSent() public {
        bytes32[] memory proof = new bytes32[](0);
        mc.claim(alice, 1000, proof);          // succeeds, transfer returns false
        assertTrue(mc.hasClaimed(alice));       // state committed
        assertEq(tok.balanceOf(alice), 0);      // nothing delivered
        vm.expectRevert(MerkleClaim.AlreadyClaimed.selector);
        mc.claim(alice, 1000, proof);           // permanently blocked
    }
}
```

This demonstrates the committed-state/unchecked-transfer divergence: after one call, `hasClaimed` is irreversibly true while the claimee holds 0 tokens, and only the honest multisig's post-60-day `sweep` can recover the funds.