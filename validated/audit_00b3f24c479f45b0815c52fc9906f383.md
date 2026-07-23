### Title
Pusher delegation signature replay allows creator to re-establish a revoked delegation, bypassing `revokePusher()` and causing feed staleness in downstream pools — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` in `CompressedOracle.sol` verifies a pusher's EIP-191 consent signature but tracks no nonce and marks no signature as consumed. A creator who saved the original consent can replay it — with the same deadline, still valid — immediately after the pusher calls `revokePusher()`, silently re-establishing the delegation the pusher just cancelled. The pusher's revocation is ineffective for the entire remaining lifetime of the original deadline, and any pool whose price provider reads from the pusher's own namespace will see stale data and revert.

---

### Finding Description

`allowPushers` signs over `(block.chainid, address(this), deadline, pusher, msg.sender)`: [1](#0-0) 

There is no nonce, no per-pusher revocation counter, and no "used-signature" set. The only freshness gate is the deadline, which the code's own comment claims is sufficient:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [2](#0-1) 

The comment is wrong. The deadline only blocks an *expired* signature. A signature whose deadline is still in the future is unconditionally replayable. `revokePusher` clears `namespaceRemapping[pusher]` to `address(0)`: [3](#0-2) 

But `allowPushers` performs no check on the current value of `namespaceRemapping[pusher]` before overwriting it: [4](#0-3) 

So the creator can call `allowPushers` again with the identical `(deadline, pusher, signature)` tuple and restore `namespaceRemapping[pusher] = creatorA` — overwriting the zero the pusher just wrote.

The fallback push path resolves the namespace from `namespaceRemapping[msg.sender]`: [5](#0-4) 

After the replay, every push the pusher sends — even pushes the pusher intends for their own namespace — lands in `creatorA`'s namespace. The pusher's own namespace feeds receive no updates and their timestamps freeze.

`getOracleData` returns the slot-level timestamp for every position in the slot: [6](#0-5) 

`PriceProvider` and `PriceProviderL2` enforce `MAX_TIME_DELTA` staleness on the `refTime` returned by `price()`. Once the pusher's own namespace timestamp ages past `MAX_TIME_DELTA`, every `getBidAndAskPrice()` call on a provider bound to those feeds reverts `FeedStalled`, and every pool swap that calls that provider reverts. [7](#0-6) 

---

### Impact Explanation

A pool whose price provider reads from the pusher's own-namespace feeds becomes permanently DoS'd for swaps until either the original deadline expires (potentially up to the maximum deadline the pusher signed) or the pusher obtains a new signature and re-delegates. Because `price()` is the sole on-chain read path for pool swaps, a stale feed causes every swap to revert — broken core pool functionality and unusable swap flows.

---

### Likelihood Explanation

- The creator is a valid semi-trusted actor who legitimately received the pusher's signature during the original delegation.
- Saving and replaying a 65-byte signature requires zero additional capability.
- The pusher has no on-chain mechanism to invalidate the old signature before its deadline; their only recourse is to wait for expiry.
- Deadlines can be set far in the future (the code imposes no maximum deadline), so the window of exposure can be arbitrarily long.

---

### Recommendation

Add a per-pusher revocation nonce to the signed payload and increment it on every `revokePusher` / `removePushers` call:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender,
        pusherNonce[pusher]   // <-- add nonce
    ))
);

// In revokePusher / removePushers:
pusherNonce[pusher]++;        // invalidates all prior signatures
```

This ensures that any signature obtained before a revocation is unconditionally invalid after it, matching the invariant the existing comment claims the deadline provides.

---

### Proof of Concept

```
T=0   Pusher signs: hash(chainid, oracle, deadline=T+365d, pusher, creatorA)
      → sig_A

T=1   creatorA calls allowPushers(T+365d, [pusher], [sig_A])
      → namespaceRemapping[pusher] = creatorA  ✓

T=2   Pusher calls revokePusher()
      → namespaceRemapping[pusher] = address(0)  ✓

T=3   creatorA calls allowPushers(T+365d, [pusher], [sig_A])   // SAME sig
      → deadline check: T+365d > block.timestamp  ✓
      → ECDSA.recover(hash) == pusher             ✓
      → namespaceRemapping[pusher] = creatorA     ← revocation bypassed

T=4   Pusher pushes to oracle (intending own namespace)
      → fallback resolves namespaceRemapping[pusher] = creatorA
      → push lands in creatorA's namespace

      Pusher's own namespace feeds: timestamp frozen at T=2
      After MAX_TIME_DELTA seconds: PriceProvider.getBidAndAskPrice() → FeedStalled
      Pool swaps revert.
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L101-117)
```text
    function getOracleData(bytes32 feedId) public view override returns (OracleData memory data) {
        (address creator, uint8 slotIndex, uint8 positionIndex) = _unpackFeedId(feedId);

        SlotLayout memory _layout = _loadSlotLayout(_oracleSlot(creator, slotIndex));
        CompressedOracleData memory compressed = _selectCompressedData(_layout, positionIndex);

        if (compressed.s1 == 0xff && compressed.s0 == 0xff) {
            data.spread1 = BPS_BASE;
            data.spread0 = BPS_BASE;
            return data;
        }

        data.price = U64x32.decode(compressed.p);
        data.spread0 = _decodeCodebookIndex(compressed.s0);
        data.spread1 = _decodeCodebookIndex(compressed.s1);
        data.timestampMs = _layout.timestampMs;
    }
```

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

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L34-34)
```text
    uint256 public immutable MAX_TIME_DELTA;
```
