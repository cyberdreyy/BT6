### Title
`allowPushers` Signature Replay After `revokePusher` Permanently Nullifies Pusher Self-Revocation Until Deadline Expiry - (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary

`allowPushers` verifies an EIP-191 pusher-consent signature but tracks no nonce and marks no signature as consumed. A creator who holds a valid (non-expired) consent signature can replay it an unlimited number of times before the deadline, re-establishing `namespaceRemapping[pusher] = creator` immediately after every `revokePusher()` call. The pusher's self-revocation is therefore ineffective for the entire lifetime of the signed deadline, which is the exact state-change-invalidates-prior-consent pattern from the BeaconKit deposit bug.

### Finding Description

`allowPushers` signs consent over `(block.chainid, address(this), deadline, pusher, msg.sender)`: [1](#0-0) 

There is no nonce, no consumed-signature set, and no per-pusher revocation counter. The only replay guard is `_ensureDeadline(deadline)`, which only blocks replay *after* the deadline, not before it.

The code's own NatSpec acknowledges the risk: [2](#0-1) 

But the mitigation is incomplete. The deadline prevents replay of an *expired* signature; it does nothing to prevent replay of a *live* signature after the pusher has revoked.

`revokePusher` clears the mapping: [3](#0-2) 

Because `allowPushers` re-writes `namespaceRemapping[pusher] = msg.sender` unconditionally on every successful call, the creator can immediately undo the revocation by submitting the same calldata again.

The `fallback` push path resolves the namespace at call time: [4](#0-3) 

So every push the pusher makes after their revocation is silently redirected back into the creator's namespace, and the pusher's own namespace receives no new data.

### Impact Explanation

**Medium** — Two concrete consequences reach the allowed impact gate:

1. **Pool feed goes stale / pool halts.** If a pool's `PriceProvider` or `AnchoredPriceProvider` is bound to a `feedId` derived from the pusher's own address (`feedIdOf(pusher, slot, pos)`), and the pusher revokes intending to redirect their pushes there, the creator's replay keeps all pushes landing in the creator's namespace. The pusher's own namespace timestamp never advances; the staleness check in `_readLeg` / `_isStale` fires; `getBidAndAskPrice` reverts `FeedStalled`; the pool becomes unusable for swaps and liquidity operations.

2. **Pusher cannot halt a bad-price stream.** If the pusher discovers their price calculation is wrong and calls `revokePusher` to stop feeding the creator's pool, the creator replays the signature and the erroneous prices continue to reach the pool until the deadline expires. The window can be as long as the deadline the pusher originally agreed to (no upper bound is enforced by the contract).

### Likelihood Explanation

**Medium** — The creator must have retained the original consent bytes (trivially true: it is calldata from a past transaction, permanently on-chain). The pusher must have revoked. The deadline must not yet have expired. All three conditions are realistic in any production deployment where pushers are rotated or disputes arise.

### Recommendation

Track consumed signatures. The simplest fix is a `mapping(bytes32 => bool) private _usedConsents` keyed on the full digest, set to `true` on first use and checked before `namespaceRemapping` is written. Alternatively, include a per-pusher monotonic nonce in the signed payload and store the last-seen nonce, so any previously issued signature is automatically invalidated when the pusher increments their nonce (e.g., by revoking).

### Proof of Concept

```
1. creator calls allowPushers(deadline=T+1day, [pusher], [sig])
   → namespaceRemapping[pusher] = creator  ✓

2. pusher calls revokePusher()
   → namespaceRemapping[pusher] = address(0)  ✓

3. creator calls allowPushers(deadline=T+1day, [pusher], [sig])  // SAME calldata
   → _ensureDeadline passes (deadline still in future)
   → ECDSA.recover returns pusher  ✓
   → namespaceRemapping[pusher] = creator  ← revocation undone

4. pusher's fallback push: creator = namespaceRemapping[pusher] = creator
   → push lands in creator namespace, NOT pusher's own namespace
   → feedIdOf(pusher, slot, pos) timestamp never advances → stale → FeedStalled

Steps 2-3 can repeat indefinitely until block.timestamp >= T+1day.
``` [5](#0-4) [3](#0-2) [6](#0-5)

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
