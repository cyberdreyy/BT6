### Title
Pusher Revocation Bypass via Signature Replay Allows Creator to Silently Re-Establish Delegation After `revokePusher()` — (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary
The `allowPushers` function binds the pusher's EIP-191 consent signature to `(chainid, address(this), deadline, pusher, creator)` but includes **no nonce or one-time-use flag**. A creator who holds a valid, unexpired signature can call `allowPushers` repeatedly — including immediately after the pusher calls `revokePusher()` — to silently re-write `namespaceRemapping[pusher] = creator`. The pusher's revocation is therefore ineffective for the entire lifetime of the deadline, and any pool that reads from the pusher's own namespace will receive stale prices.

### Finding Description

`allowPushers` verifies:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

The signed message commits to `deadline` but **not** to the current value of `namespaceRemapping[pusher]` and **not** to a per-delegation nonce. The same `(deadline, pusher, creator)` tuple is valid for every call to `allowPushers` until `block.timestamp > deadline`.

`revokePusher` correctly zeroes the mapping:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [2](#0-1) 

But nothing prevents the creator from immediately calling `allowPushers` again with the identical signature, restoring `namespaceRemapping[pusher] = creator`. The code's own comment acknowledges the risk ("an undated signature could re-establish a delegation AFTER the pusher revoked it") but the deadline only bounds the *outer* window — it does not prevent replay within that window. [3](#0-2) 

The `fallback` push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

So every push after the creator's replay lands in the creator's namespace, not the pusher's own namespace. Any pool or price provider reading from `feedIdOf(pusher, slotIndex, positionIndex)` will see a timestamp that never advances — i.e., stale data.

### Impact Explanation

A pool whose `IPriceProvider` is configured to read from the pusher's own namespace (e.g., `feedIdOf(pusher, s, p)`) will receive a price whose `refTime` is frozen at the last update before the delegation was first established. Price providers enforce a `maxTimeDrift` staleness check; once the drift window closes, every `getBidAndAskPrice` call reverts, making the pool's `swap` path permanently unusable until the pusher either stops pushing or the deadline expires. This satisfies the "broken core pool functionality causing unusable swap flows" impact criterion.

### Likelihood Explanation

Likelihood is **medium**. The attack requires:
1. The pusher to have signed consent with a deadline far enough in the future that the creator can replay it after revocation (common in production where operators use 24 h–7 d deadlines for operational convenience).
2. The creator to be adversarial or compromised.
3. A pool to be reading from the pusher's own namespace.

All three conditions are plausible in a live deployment.

### Recommendation

Bind the signature to a per-pusher nonce stored in the contract, or to the current value of `namespaceRemapping[pusher]` (e.g., include `namespaceRemapping[pusher]` in the signed payload so that a revocation changes the expected pre-image). Alternatively, mark each `(pusher, creator, deadline)` tuple as consumed after first use with a `mapping(bytes32 => bool) usedConsents` flag, so the signature cannot be replayed even within the deadline window.

### Proof of Concept

```
1. Pusher signs: keccak256(abi.encode(chainid, oracle, deadline=T+7days, pusher, creatorA))
2. CreatorA calls allowPushers(T+7days, [pusher], [sig])
   → namespaceRemapping[pusher] = creatorA  ✓
3. Pusher calls revokePusher()
   → namespaceRemapping[pusher] = address(0)  ✓ (pusher believes they are free)
4. CreatorA immediately calls allowPushers(T+7days, [pusher], [sig])  ← SAME sig, still valid
   → namespaceRemapping[pusher] = creatorA  ← revocation silently undone
5. Pusher continues pushing (e.g., for their own pool reading feedIdOf(pusher, s, p))
   → fallback resolves namespace = creatorA
   → writes land in creatorA's slots, NOT pusher's slots
6. Pool reading feedIdOf(pusher, s, p) sees timestamp frozen → staleness revert on every swap
``` [5](#0-4) [6](#0-5) [7](#0-6)

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
