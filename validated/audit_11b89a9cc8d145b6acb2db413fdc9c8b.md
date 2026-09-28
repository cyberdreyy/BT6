### Title
Unchecked ERC20 `transfer` return value permanently burns claims — (File: contracts/MerkleClaim.sol)

### Summary

`MerkleClaim.claim` marks a claimee as claimed (`hasClaimed[to] = true`) *before* performing a raw `IERC20Upgradeable(token).transfer(to, amount)` whose boolean return value is never checked. If the distributed token returns `false` instead of reverting (e.g., insufficient distributor balance on a non-reverting ERC20), the call succeeds, the claim is consumed, and the claimee receives nothing — permanently, because the `AlreadyClaimed()` guard blocks any retry. This is the same bug class as the external report: a token transfer whose success is not enforced by `require`/`safeTransfer`, causing silent failure at the exact point where state is irrevocably mutated. [1](#0-0) 

### Finding Description

In `claim()`:

```solidity
// contracts/MerkleClaim.sol
hasClaimed[to] = true;                                  // L63 — state mutated first
IERC20Upgradeable(token).transfer(to, amount);          // L66 — return value unchecked
```

The same pattern exists in `sweep()` at L74, though that path is multisig-gated. The `claim` path is callable by anyone for any claimee in the Merkle tree.

Notably, the rest of the codebase consistently uses `SafeERC20Upgradeable.safeTransfer`/`safeTransferFrom` for exactly this reason (e.g., `IdleCreditVault`, `IdleCDOEpochQueue`, `DefaultDistributor`, `WriteOffEscrow` — 19, 16, 8, and 16 usages respectively), so `MerkleClaim` is the outlier. `IdleStrategy.sol` also has unchecked `_idleToken.transfer/transferFrom` calls (L85, L105, L109, L155, L201), but that strategy targets IdleTokens, which are standard reverting ERC20s, making it unexploitable there; `MerkleClaim` distributes an arbitrary token set at `initialize`.

### Impact Explanation

Permanent freezing / loss of unclaimed yield for the affected claimee. If `transfer` returns `false` (non-reverting token semantics, or a token that returns `false` on insufficient balance when the claim contract is underfunded relative to the Merkle root's total allocations — a state reachable legitimately if the funder underfunds or if an earlier `sweep` is front-run after 60 days), the claimee's full `amount` entitlement is destroyed: the proof cannot be reused and the tokens remain in the contract, later sweepable by `TL_MULTISIG` at L69–75. The "one receipt, one payout" invariant is broken — the receipt (hasClaimed) is burned with zero payout. Loss is the claimee's entire claim amount.

### Likelihood Explanation

Requires the distributed `token` to be a false-returning ERC20 rather than a reverting one. `token` is fixed at initialize by the honest deployer, so likelihood is conditional on the deployed token's semantics and on balance shortfall. No privileged misbehavior is needed; the victim path is triggered by an ordinary `claim` call. Medium-low likelihood, but the failure is silent and irreversible, matching the medium severity of the external report.

### Recommendation

Wrap the transfer in `SafeERC20Upgradeable` (add `using SafeERC20Upgradeable for IERC20Upgradeable;`) or check the return value:

```solidity
// contracts/MerkleClaim.sol L66
IERC20Upgradeable(token).safeTransfer(to, amount);
// and L74
IERC20Upgradeable(_token).safeTransfer(to, IERC20Upgradeable(_token).balanceOf(address(this)));
```

This both enforces the return value and reverts the whole tx — including the `hasClaimed` write — on failure, preserving the claimee's ability to retry.

### Proof of Concept

```solidity
// SPDX-License-Identifier: AGPL-3.0-only
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import "../contracts/MerkleClaim.sol";
import "@openzeppelin/contracts/token/ERC20/ERC20.sol";

/// Token that returns false instead of reverting on insufficient balance
contract FalseOnFailToken is ERC20 {
    constructor() ERC20("FFT", "FFT") { _mint(msg.sender, 1e24); }
    function transfer(address to, uint256 amount) public override returns (bool) {
        if (balanceOf(msg.sender) < amount) return false;
        return super.transfer(to, amount);
    }
}

contract MerkleClaimUncheckedTransferTest is Test {
    MerkleClaim claim;
    FalseOnFailToken token;
    address alice = address(0xA11CE);
    uint256 amount = 100e18;

    function setUp() public {
        token = new FalseOnFailToken();
        claim = new MerkleClaim();
        // Build single-leaf tree: leaf = keccak256(bytes.concat(keccak256(abi.encode(alice, amount))))
        bytes32 leaf = keccak256(bytes.concat(keccak256(abi.encode(alice, amount))));
        claim.initialize(leaf, address(token));
        // Underfund: send less than the claim amount so transfer returns false
        token.transfer(address(claim), amount - 1);
    }

    function testClaimBurnedOnFalseReturn() public {
        bytes32[] memory proof = new bytes32[](0); // leaf == root, empty proof
        claim.claim(alice, amount, proof);          // does NOT revert
        assertEq(token.balanceOf(alice), 0);        // alice got nothing
        assertTrue(claim.hasClaimed(alice));        // claim permanently consumed
        vm.expectRevert(MerkleClaim.AlreadyClaimed.selector);
        claim.claim(alice, amount, proof);          // retry impossible
    }
}
```

Note: `MerkleClaim` is a periphery distribution contract rather than core credit-vault code; if it is deemed out of scope as a utility, the in-scope core contracts (`IdleCreditVault`, `IdleCDOEpochQueue`, `WriteOffEscrow`, `DefaultDistributor`, `ProgrammableBorrower`) all consistently use `SafeERC20Upgradeable`, and no unchecked-transfer analog exists there. The only other raw transfers are in `IdleStrategy.sol` (legacy strategy, excluded) where the callee IdleToken reverts on failure anyway.

### Citations

**File:** contracts/MerkleClaim.sol (L52-67)
```text
  function claim(address to, uint256 amount, bytes32[] calldata proof) external {
    // Throw if address has already claimed tokens
    if (hasClaimed[to]) revert AlreadyClaimed();

    // Verify merkle proof, or revert if not in tree,
    // double keccak is preferred https://github.com/OpenZeppelin/merkle-tree#user-content-fn-1-786ed85226485d53b8a7834b144c1642
    bytes32 leaf = keccak256(bytes.concat(keccak256(abi.encode(to, amount))));
    bool isValidLeaf = MerkleProofUpgradeable.verify(proof, merkleRoot, leaf);
    if (!isValidLeaf) revert NotInMerkle();

    // Set address to claimed
    hasClaimed[to] = true;

    // Transfer tokens to claimee
    IERC20Upgradeable(token).transfer(to, amount);
  }
```
