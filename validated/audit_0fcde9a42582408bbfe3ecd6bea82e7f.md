### Title

Unvalidated or attacker-controlled `merkleRoot` can freeze all claimable tokens - (File: `contracts/MerkleClaim.sol`)

### Summary

`MerkleClaim.initialize` accepts `bytes32(0)` as the claimee root and is callable by any unprivileged account while `token == address(0)`. [1](#0-0)  An attacker who initializes a freshly deployed or cloned instance before the intended initializer can install `bytes32(0)`—or any unusable root—and select the token address, after which legitimate Merkle claims always fail. [2](#0-1)  Tokens held by the contract cannot be recovered by users and remain frozen until `TL_MULTISIG` can call `sweep` more than 60 days after the malicious initialization. [3](#0-2) 

### Finding Description

The initializer’s only persistence check is `token == address(0)`; it does not validate either `_merkleRoot` or `_token`, and it does not authenticate `msg.sender`. [1](#0-0)  `claim` derives the expected leaf and calls `MerkleProofUpgradeable.verify` directly against the stored root, without checking whether that root was configured. [2](#0-1) 

With `merkleRoot == bytes32(0)`, a valid distribution cannot be constructed for an ordinary nonzero leaf; every claim reverts with `NotInMerkle`. [4](#0-3)  More generally, because initialization is permissionless, an attacker can front-run the legitimate initialization and choose a root for which no valid recipient proof exists. [1](#0-0) 

The broken invariant is one-receipt-one-payout/distribution availability: token balances can be escrowed by the claim contract while all intended claims are permanently rejected. The only recovery path is delayed multisig sweeping, which does not restore recipient-specific distribution and is unavailable for the first 60 days. [3](#0-2) 

### Impact Explanation

An unprivileged EOA can cause temporary freezing of 100% of the ERC-20 balance sent to an uninitialized claim instance. The freeze lasts at least 60 days from initialization because `sweep` enforces `block.timestamp > deployTime + 60 days`. [3](#0-2) 

If the contract is intended to hold undistributed or unclaimed yield, all of that balance is unavailable to recipients during the freeze. The attacker does not need a valid proof, recipient status, KYC approval, tranche balance, or privileged role.

### Likelihood Explanation

The attack requires a deployment workflow in which `initialize` is not executed atomically with deployment or is otherwise front-runnable. The function is explicitly public and clone-oriented, but has no initializer role, factory authentication, zero-root rejection, or zero-token rejection. [5](#0-4) [1](#0-0) 

Once the attacker’s initialization succeeds, later legitimate initialization is blocked because `token` is nonzero. [6](#0-5)  Funding the contract afterward makes the freeze actionable; funding before initialization allows the attacker to select that already-funded token as `_token`.

### Recommendation

Validate configuration and bind initialization to the intended deployer or factory:

```solidity
function initialize(bytes32 _merkleRoot, address _token) external {
    require(msg.sender == EXPECTED_INITIALIZER, "unauthorized");
    require(token == address(0), "initialized");
    require(_merkleRoot != bytes32(0), "zero root");
    require(_token != address(0), "zero token");

    merkleRoot = _merkleRoot;
    token = _token;
    deployTime = block.timestamp;
}
```

Preferably initialize atomically through a factory or use a constructor when cloning is not required. A nonzero-root check alone is insufficient because an attacker can still install an arbitrary nonzero root during permissionless initialization.

### Proof of Concept

```solidity
// SPDX-License-Identifier: AGPL-3.0-only
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {MerkleClaim} from "../contracts/MerkleClaim.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

contract ClaimToken is ERC20 {
    constructor() ERC20("Claim", "CLM") {
        _mint(msg.sender, 1_000_000 ether);
    }
}

contract MerkleClaimZeroRootTest is Test {
    MerkleClaim internal claim;
    ClaimToken internal token;

    address internal attacker = makeAddr("attacker");
    address internal recipient = makeAddr("recipient");

    function testZeroRootFreezesDistributionUntilSweep() public {
        claim = new MerkleClaim();
        token = new ClaimToken();

        // An unprivileged caller initializes the cloneable claim contract first.
        vm.prank(attacker);
        claim.initialize(bytes32(0), address(token));

        // Fund the misconfigured distribution.
        token.transfer(address(claim), 100 ether);

        // Every claim fails because no normal leaf verifies against bytes32(0).
        bytes32[] memory proof = new bytes32[](0);
        vm.expectRevert(MerkleClaim.NotInMerkle.selector);
        vm.prank(recipient);
        claim.claim(recipient, 100 ether, proof);

        // Even the trusted multisig cannot recover the balance for 60 days.
        address multisig = claim.TL_MULTISIG();
        vm.prank(multisig);
        vm.expectRevert();
        claim.sweep(multisig);

        skip(60 days + 1);

        vm.prank(multisig);
        claim.sweep(multisig);

        assertEq(token.balanceOf(address(claim)), 0);
        assertEq(token.balanceOf(multisig), 100 ether);
        assertEq(token.balanceOf(recipient), 0);
    }
}
```

### Citations

**File:** contracts/MerkleClaim.sol (L9-12)
```text
/// @title MerkleClaim
/// @notice Modified from https://github.com/Anish-Agnihotri/merkle-airdrop-starter/blob/master/contracts/src/MerkleClaimERC20.sol (no ERC20, cloneable)
/// @author This version @bugduino . Original: Anish Agnihotri <contact@anishagnihotri.com>
contract MerkleClaim {
```

**File:** contracts/MerkleClaim.sol (L37-43)
```text
  function initialize(bytes32 _merkleRoot, address _token) public {
    require(token == address(0)); // Ensure token is not set

    merkleRoot = _merkleRoot; // Update root
    token = _token;

    deployTime = block.timestamp;
```

**File:** contracts/MerkleClaim.sol (L52-60)
```text
  function claim(address to, uint256 amount, bytes32[] calldata proof) external {
    // Throw if address has already claimed tokens
    if (hasClaimed[to]) revert AlreadyClaimed();

    // Verify merkle proof, or revert if not in tree,
    // double keccak is preferred https://github.com/OpenZeppelin/merkle-tree#user-content-fn-1-786ed85226485d53b8a7834b144c1642
    bytes32 leaf = keccak256(bytes.concat(keccak256(abi.encode(to, amount))));
    bool isValidLeaf = MerkleProofUpgradeable.verify(proof, merkleRoot, leaf);
    if (!isValidLeaf) revert NotInMerkle();
```

**File:** contracts/MerkleClaim.sol (L69-74)
```text
  function sweep(address to) public {
    require(msg.sender == TL_MULTISIG);
    // allow sweep after 60 days
    require(block.timestamp > deployTime + 60 days);
    address _token = token;
    IERC20Upgradeable(_token).transfer(to, IERC20Upgradeable(_token).balanceOf(address(this)));
```
