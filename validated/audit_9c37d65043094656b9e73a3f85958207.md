### Title
Duplicate recipient entries in the airdrop Merkle tree cause permanently stuck tokens - (File: contracts/MerkleClaim.sol)

### Summary
`MerkleClaim.claim` tracks claimed leaves via `hasClaimed[to]` keyed only on the recipient address, while the Merkle leaf encodes `(to, amount)`. If the off-chain tree construction creates two leaves for the same address (e.g., vesting splits, duplicate rows), only the first claim succeeds and the second reverts with `AlreadyClaimed`, leaving the excess tokens locked in the contract. This is the same bug class as the Sablier Merkle index collision: the "already claimed" marker is not bound to the leaf/proof path, so distinct valid leaves collide on the same claim key.

### Finding Description
The leaf and claim-tracking logic:

```solidity
// contracts/MerkleClaim.sol
mapping(address => bool) public hasClaimed;              // L23

function claim(address to, uint256 amount, bytes32[] calldata proof) external {
    if (hasClaimed[to]) revert AlreadyClaimed();          // L54
    bytes32 leaf = keccak256(bytes.concat(keccak256(abi.encode(to, amount)))); // L58
    bool isValidLeaf = MerkleProofUpgradeable.verify(proof, merkleRoot, leaf);
    if (!isValidLeaf) revert NotInMerkle();
    hasClaimed[to] = true;                               // L63
    IERC20Upgradeable(token).transfer(to, amount);       // L66
}
```

Nothing prevents the tree builder from including the same `to` in multiple leaves with different `amount`s — exactly analogous to the unenforceable "same index in multiple leaves" condition in SablierV2MerkleLL/LT. There is no on-chain check that addresses are unique in the tree. An attacker (any unprivileged user with a valid proof) can even deliberately front-run a victim's larger claim if the tree has two leaves for the victim: claiming the smaller leaf first marks `hasClaimed[victim] = true`, bricking the larger leaf.

### Impact Explanation
For any address that appears in N leaves, only the first claimed amount is ever released; the tokens backing the remaining leaves are stuck in `MerkleClaim`. The contract is funded with the total of all leaves, so the unfundable remainder sits idle. The only escape is `sweep` (L69–L75), callable solely by `TL_MULTISIG` after `deployTime + 60 days`, so funds are frozen for at least 60 days and recovery depends entirely on the multisig acting — there is no user-level path to the tokens. Quantified loss: `sum(amount_i)` for all duplicate leaves beyond the first claimed one per address.

### Likelihood Explanation
Same as the Sablier finding: correctness relies entirely on honest off-chain tree construction, and duplicates arise naturally when allocations are split across multiple records for one recipient. Additionally, unlike Sablier, the colliding claim here can be triggered adversarially: anyone can call `claim(to, ...)` for any `to`, so a griefer can select which leaf a duplicated address gets to claim (always the smallest), guaranteeing the larger remainder is stuck rather than leaving it to chance.

### Recommendation
Include a unique leaf index in the leaf encoding and track `claimed` by index (bitmap), or better, adopt the Eigenlayer-style proof-path verification where the index is supplied alongside the proof so each position in the tree can be claimed at most once regardless of leaf contents:

```solidity
bytes32 leaf = keccak256(bytes.concat(keccak256(abi.encode(index, to, amount))));
if (claimed[index]) revert AlreadyClaimed();
claimed[index] = true;
```

Alternatively, enforce address uniqueness during tree generation and sum duplicate allocations into a single leaf before publishing the root.

### Proof of Concept
```solidity
// test/foundry/MerkleClaimDup.t.sol
// SPDX-License-Identifier: MIT
pragma solidity 0.8.10;
import "forge-std/Test.sol";
import "../../contracts/MerkleClaim.sol";
import "@openzeppelin/contracts/token/ERC20/ERC20.sol";

contract T is ERC20 { constructor() ERC20("T","T") { _mint(msg.sender, 1000e18); } }

contract MerkleClaimDup is Test {
    // Two leaves, SAME address, different amounts:
    //   leaf0 = hash(hash(abi.encode(victim, 100)))
    //   leaf1 = hash(hash(abi.encode(victim, 900)))
    // root = hash(leaf0 || leaf1). Tree funded with 1000.
    function testDuplicateAddressStuck() public {
        T token = new T();
        address victim = address(0xBEEF);
        bytes32 leaf0 = keccak256(bytes.concat(keccak256(abi.encode(victim, uint256(100e18)))));
        bytes32 leaf1 = keccak256(bytes.concat(keccak256(abi.encode(victim, uint256(900e18)))));
        bytes32 root = leaf0 < leaf1
            ? keccak256(abi.encodePacked(leaf0, leaf1))
            : keccak256(abi.encodePacked(leaf1, leaf0));

        MerkleClaim mc = new MerkleClaim();
        mc.initialize(root, address(token));
        token.transfer(address(mc), 1000e18);

        // Anyone can grief: claim the SMALL leaf for victim
        bytes32[] memory proof0 = new bytes32[](1); proof0[0] = leaf1;
        vm.prank(address(0xCAFE));
        mc.claim(victim, 100e18, proof0);
        assertEq(token.balanceOf(victim), 100e18);

        // Victim's larger legitimate leaf is now unclaimable
        bytes32[] memory proof1 = new bytes32[](1); proof1[0] = leaf0;
        vm.prank(victim);
        vm.expectRevert(MerkleClaim.AlreadyClaimed.selector);
        mc.claim(victim, 900e18, proof1);

        // 900e18 stuck; only multisig sweep after 60 days can recover
        assertEq(token.balanceOf(address(mc)), 900e18);
    }
}
```