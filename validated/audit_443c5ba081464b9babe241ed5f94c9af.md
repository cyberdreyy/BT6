### Title
Creator Can Replay Pusher Consent Signature Within Deadline Window to Bypass Revocation — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` checks only that the deadline has not expired, but contains no nonce or used-signature registry. A creator who holds a valid, unexpired pusher consent signature can call `allowPushers` again with the identical signature after the pusher has called `revokePusher()`, silently re-establishing the delegation the pusher explicitly cancelled. The code's own comment acknowledges the risk but the deadline alone does not close it.

---

### Finding Description

`allowPushers` signs consent over the tuple `(chainid, address(this), deadline, pusher, creator)`: [1](#0-0) 

`_ensureDeadline` only checks `block.timestamp <= deadline`: [2](#0-1) 

`revokePusher` clears the mapping but records nothing about the revocation: [3](#0-2) 

Because the signed tuple contains no nonce and no revocation flag, the exact same `(deadline, sig)` pair that was used in the original `allowPushers` call remains cryptographically valid for the entire deadline window. The creator can call `allowPushers` a second time with the identical arguments and the function will:

1. Pass `_ensureDeadline` (deadline still in the future).
2. Recover the pusher's address from the unchanged signature.
3. Write `namespaceRemapping[pusher] = creator` again.

The comment at line 186–191 explicitly warns that "an undated signature could re-establish a delegation AFTER the pusher revoked it" and claims the deadline prevents this — but the deadline only prevents replay *after* it expires, not *within* the window. [4](#0-3) 

---

### Impact Explanation

Once the delegation is silently re-established, the `fallback` push path resolves the namespace from `namespaceRemapping[msg.sender]`: [5](#0-4) 

Any subsequent push by the pusher (e.g., a bot pushing to its own namespace for its own pools) is redirected into the creator's namespace. The pusher's own namespace receives no updates, so any pool reading from `feedIdOf(pusher, slotIndex, positionIndex)` will see a stale timestamp. `getOracleData` returns `timestampMs = 0` for a never-updated slot, which every price provider rejects as stale: [6](#0-5) 

This causes `getBidAndAskPrice` to revert with `FeedStalled`, halting swaps on the pusher's pools. Simultaneously, the creator's pools receive price data the pusher did not intend to supply after revocation — a silent feed hijack.

---

### Likelihood Explanation

The creator holds the pusher's consent signature from the original `allowPushers` call. No additional on-chain action is required from the pusher. The creator simply re-submits the same calldata. The window is as long as the original deadline (up to any value the creator chose — the contract imposes no cap on deadline length). The attack is fully permissionless for the creator and requires no special tooling.

---

### Recommendation

Add a per-pusher revocation nonce or a `revokedAt` timestamp to the oracle state. Include the nonce in the signed digest so that a post-revocation replay produces a different hash that no longer matches the stored signature. Alternatively, record a `revokedNonce[pusher]` counter that is incremented on every `revokePusher` / `removePushers` call and require the signed nonce to equal the current value:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender, pusherNonce[pusher]))

// In revokePusher / removePushers:
pusherNonce[pusher]++;
namespaceRemapping[pusher] = address(0);
```

---

### Proof of Concept

```solidity
// 1. Creator delegates pusher with a 1-day deadline
uint256 deadline = block.timestamp + 1 days;
bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);
address[] memory pushers = new address[](1);
pushers[0] = pusher;
bytes[] memory sigs = new bytes[](1);
sigs[0] = sig;

vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs);
assertEq(oracle.namespaceRemapping(pusher), creator);

// 2. Pusher revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0));

// 3. Creator replays the IDENTICAL (deadline, sig) — succeeds, revocation bypassed
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs);
assertEq(oracle.namespaceRemapping(pusher), creator); // delegation re-established

// 4. Pusher's next push lands in creator's namespace, not pusher's own
uint56 tsMs = uint56(block.timestamp * 1000);
uint48 raw = _packRaw(1_000_000, 5, 3);
vm.prank(pusher);
(bool ok,) = address(oracle).call(_wordAt(0, 0, raw, tsMs));
assertTrue(ok);

// Pusher's own namespace is empty → stale → FeedStalled on pusher's pools
assertEq(oracle.getOracleData(oracle.feedIdOf(pusher, 0, 0)).price, 0);
// Creator's namespace received the hijacked price
assertEq(oracle.getOracleData(oracle.feedIdOf(creator, 0, 0)).price,
         U64x32.decode(uint32(raw >> 16)));
```

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-321)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;

        assembly ("memory-safe") {
            end := calldatasize()
            namespace := shl(96, creator) // [creator:20][zeros:12]
        }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L124-126)
```text
    function _ensureDeadline(uint256 deadline) internal view virtual {
        require(block.timestamp <= deadline, DeadlineExceeded());
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L282-284)
```text
        // Stale reference → not ok. Clamping to a stale anchor is the one false-safety case.
        if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);

```
