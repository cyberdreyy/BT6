### Title
`revokePusher()` Self-Revocation Bypassed by Creator Signature Replay Before Deadline — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

The `revokePusher()` function is designed to let a pusher permanently exit a creator's namespace. However, because `allowPushers` performs no used-signature tracking and no revocation-state check, a creator who still holds a valid (non-expired) consent signature can immediately re-establish the delegation after the pusher revokes. The pusher's self-revocation is therefore ineffective for the entire lifetime of the original deadline, and any prices the pusher subsequently pushes—believing they are writing to their own namespace—are silently redirected into the creator's namespace and can reach live pool swaps as corrupted quotes.

---

### Finding Description

`allowPushers` validates three things and nothing more:

1. `block.timestamp <= deadline` (via `_ensureDeadline`)
2. `pusher != msg.sender`
3. The pusher's EIP-191 signature over `(chainid, oracle, deadline, pusher, creator)` is valid [1](#0-0) 

There is no check that `namespaceRemapping[pusher]` is currently zero, and no record of previously consumed signatures. After `revokePusher()` clears the mapping: [2](#0-1) 

the creator can call `allowPushers` again with the **identical** signature and deadline, passing all three checks and writing `namespaceRemapping[pusher] = creator` again. The code comment acknowledges the problem but incorrectly claims the deadline is the solution:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [3](#0-2) 

The deadline only bounds the window; it does not prevent re-establishment **within** that window. A pusher who signs a consent with a deadline days or weeks in the future (a normal operational choice) has no on-chain mechanism to permanently exit the creator's namespace until that deadline passes.

The fallback push path resolves the namespace at call time: [4](#0-3) 

So every push the pusher makes after believing they have revoked—and after the creator has silently re-established the mapping—lands in the creator's namespace, not the pusher's own. If the pusher has begun writing prices for a different asset pair (their own feeds), those prices overwrite the creator's feed slots. The `price()` read path then returns those corrupted values to `AnchoredPriceProvider`, which forwards them as bid/ask quotes to pool swaps. [5](#0-4) 

---

### Impact Explanation

A pool whose `AnchoredPriceProvider` reads from a `CompressedOracleV1` feed controlled by a malicious creator receives a bid/ask derived from the wrong asset's price. The clamp in `AnchoredPriceProvider` is anchored to the same corrupted oracle, so it provides no protection. The pool executes swaps at the wrong price, causing direct loss of user principal or LP assets. This matches the "bad-price execution" and "swap conservation failure" impact classes.

---

### Likelihood Explanation

The trigger is a semi-trusted creator who:
1. Holds a still-valid (non-expired) consent signature from the pusher, and
2. Calls `allowPushers` again after the pusher revokes.

Both conditions are reachable without any privileged role. Consent signatures are routinely issued with multi-day or multi-week deadlines for operational convenience, making the replay window large. The pusher has no on-chain way to detect the re-establishment without polling `namespaceRemapping` before every push.

---

### Recommendation

Track consumed signatures with a per-pusher nonce or a `mapping(bytes32 => bool) usedSignatures` set, and mark the signature hash as spent inside `allowPushers`. Alternatively, store a per-pusher `revokedAt` timestamp in `revokePusher()` and reject any consent signature whose deadline was issued before `revokedAt`. Either approach makes self-revocation permanent within the original deadline window.

---

### Proof of Concept

```
// 1. Pusher P signs consent for creator C, deadline = block.timestamp + 7 days
bytes memory sig = sign(PUSHER_KEY, chainid, oracle, deadline, pusher, creator);

// 2. C establishes delegation
oracle.allowPushers(deadline, [pusher], [sig]);
// namespaceRemapping[pusher] == creator  ✓

// 3. P self-revokes
vm.prank(pusher);
oracle.revokePusher();
// namespaceRemapping[pusher] == address(0)  ✓

// 4. C replays the SAME signature (deadline still valid)
oracle.allowPushers(deadline, [pusher], [sig]);
// namespaceRemapping[pusher] == creator  ← revocation bypassed

// 5. P pushes prices for their own new feed (e.g. BTC/USD),
//    believing they write to their own namespace.
//    The fallback resolves namespaceRemapping[pusher] = creator,
//    so the BTC/USD price lands in creator's ETH/USD slot.
//    Pools reading creator's ETH/USD feed now receive BTC/USD quotes.
vm.prank(pusher);
(bool ok,) = address(oracle).call(btcUsdSlotWord);
// oracle.getOracleData(feedIdOf(creator, ethUsdSlot)).price == btcPrice  ← bad price
``` [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L163-178)
```text
    function price(bytes32 feedId, address /* pool */)
        external
        view
        returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
    {
        return _price(feedId);
    }

    function _price(bytes32 feedId)
        internal
        view
        returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
    {
        OracleData memory data = getOracleData(feedId);
        return (uint256(data.price), uint256(data.spread0), data.spread1, data.timestampMs.toSeconds());
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
