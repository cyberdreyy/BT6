### Title
Pusher Revocation Replay: Creator Can Re-Establish Delegation After `revokePusher()` Using the Original EIP-191 Signature — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.allowPushers` accepts any valid EIP-191 pusher-consent signature whose deadline has not yet expired. Because no nonce or used-signature registry exists, a creator can call `allowPushers` a second time with the **same** signature and deadline after the pusher has self-revoked via `revokePusher()`. This silently re-establishes delegation, redirecting the pusher's future slot writes back into the creator's namespace without the pusher's current consent.

---

### Finding Description

`allowPushers` signs consent as:

```
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

The only replay guard is `_ensureDeadline(deadline)`, which rejects calls after the deadline but permits unlimited replays before it. [2](#0-1) 

`revokePusher()` clears `namespaceRemapping[msg.sender]` to `address(0)`: [3](#0-2) 

But nothing in the contract invalidates the pusher's previously issued signature. The creator can immediately call `allowPushers` again with the identical `(deadline, pusher, signature)` tuple, passing `_ensureDeadline` and the `ECDSA.recover` check, and writing `namespaceRemapping[pusher] = msg.sender` again.

The code's own comment acknowledges the problem but provides only a partial mitigation:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [4](#0-3) 

The deadline limits the replay window but does **not** prevent replay within that window. A pusher who signs a consent with a deadline one year in the future cannot effectively revoke for up to one year.

---

### Impact Explanation

After the creator replays the signature:

1. The pusher's `fallback()` pushes continue to land in the **creator's namespace** instead of the pusher's own namespace.
2. If the pusher is simultaneously operating their own pool that reads from `feedIdOf(pusher, ...)`, that pool receives **no new data** — its oracle timestamp freezes at the last pre-revocation push.
3. A frozen timestamp causes `AnchoredPriceProvider._readLeg` to return `ok = false` once `MAX_REF_STALENESS` elapses, halting swaps (`FeedStalled`), or — if `MAX_REF_STALENESS` is generous — allows the pool to execute swaps against a stale mid price. [5](#0-4) 

The pusher's pool LPs and traders are exposed to stale-price execution or a frozen swap path — a direct loss of user principal or owed LP assets.

---

### Likelihood Explanation

- The creator is an unprivileged user (not a protocol admin).
- The replay requires only a second call to the public `allowPushers` function with already-public calldata (the original transaction is on-chain).
- Pushers who sign long-lived deadlines (common for operational convenience) are fully exposed for the entire deadline window.
- The pusher has no on-chain way to detect that delegation was re-established without monitoring `PusherAuthorized` events.

---

### Recommendation

Add a per-pusher nonce to the signed message and increment it on every successful `allowPushers` call. Revocation should also increment the nonce, making all previously issued signatures for that pusher immediately invalid:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender, pusherNonce[pusher]   // ← add nonce
    ))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
pusherNonce[pusher]++;   // ← invalidate this and all prior signatures

// In revokePusher:
namespaceRemapping[msg.sender] = address(0);
pusherNonce[msg.sender]++;   // ← invalidate any outstanding consent signatures
```

---

### Proof of Concept

```solidity
// 1. Pusher signs consent with a 1-year deadline.
uint256 deadline = block.timestamp + 365 days;
bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

// 2. Creator establishes delegation.
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator);

// 3. Pusher revokes.
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0));

// 4. Creator replays the SAME signature — delegation is silently re-established.
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator); // ← revocation undone

// 5. Pusher's subsequent fallback push lands in creator's namespace, not pusher's own.
vm.prank(pusher);
(bool ok,) = address(oracle).call(_wordAt(0, 0, _packRaw(999_000, 3, 3), uint56(block.timestamp * 1000)));
assertTrue(ok);
// Pusher's own namespace is empty — their pool sees stale prices.
assertEq(oracle.getOracleData(oracle.feedIdOf(pusher, 0, 0)).price, 0);
// Creator's namespace received the data.
assertGt(oracle.getOracleData(oracle.feedIdOf(creator, 0, 0)).price, 0);
``` [2](#0-1) [3](#0-2) [6](#0-5)

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L186-212)
```text
    /// @notice Delegates pusher wallets into the caller's namespace. The pusher's EIP-191
    ///         signature is REQUIRED — without it anyone could remap a foreign pusher
    ///         wallet into their own namespace and silently swallow its pushes. The
    ///         deadline is likewise required: the signed consent carries no timestamp of
    ///         its own, so an undated signature could re-establish a delegation AFTER the
    ///         pusher revoked it.
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

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L277-295)
```text
    function _readLeg(bytes32 feedId)
        internal returns (uint256 mid, uint256 spreadBps, uint256 refTime, bool ok)
    {
        (mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);

        // Stale reference → not ok. Clamping to a stale anchor is the one false-safety case.
        if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);

        // Basic validity — mid positive, spreadBps not the stalled/off-hours marker (the Chainlink oracle
        // writes spreadBps = ORACLE_BPS when an RWA market is closed).
        if (mid == 0 || spreadBps >= ORACLE_BPS) return (mid, spreadBps, refTime, false);

        // Per-leg price guard.
        (uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(feedId);
        guardMax = guardMax == 0 ? type(uint128).max : guardMax;
        if (mid < guardMin || mid > guardMax) return (mid, spreadBps, refTime, false);

        ok = true;
    }
```
