### Title
Creator can replay a pusher's consent signature after `revokePusher()` to silently re-establish delegation and redirect feed writes — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` verifies an EIP-191 signature but keeps no used-signature registry or nonce. A creator who already holds a pusher's still-valid consent signature can call `allowPushers` again at any time before the deadline, even after the pusher has called `revokePusher()`. This instantly restores `namespaceRemapping[pusher] = creator`, so every subsequent fallback push the pusher makes—believing it targets their own namespace or a new creator's namespace—silently lands in the original creator's namespace instead, feeding that creator's price feed (and any pool backed by it) with data the pusher never intended to provide there.

---

### Finding Description

`allowPushers` constructs the signed digest as:

```
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

There is no nonce, no per-pusher revocation counter, and no set of consumed digests. The only freshness gate is `_ensureDeadline(deadline)`, which only checks `block.timestamp <= deadline`. Within that window the identical `(chainid, oracle, deadline, pusher, creator)` tuple is accepted an unlimited number of times.

`revokePusher` clears the mapping:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [2](#0-1) 

But it does not invalidate the pusher's previously issued signature. The creator still holds that signature and can immediately call `allowPushers` again with the same bytes, restoring `namespaceRemapping[pusher] = creator` before the pusher's next push transaction is mined.

The code comment itself acknowledges the risk but misidentifies the deadline as the cure:

> "The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it." [3](#0-2) 

The deadline only bounds the replay window; it does not prevent replay within that window. A pusher who signs a 30-day consent and revokes on day 1 is still fully exposed for the remaining 29 days.

The fallback push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

So every push the pusher makes after the creator's replay lands in the creator's namespace, not the pusher's own.

---

### Impact Explanation

**Stale / wrong prices reach a pool backed by the creator's namespace.**

Concrete scenario:

1. Pusher signs consent for Creator A with `deadline = now + 30 days`.
2. Creator A calls `allowPushers` → `namespaceRemapping[pusher] = creatorA`. Pusher feeds `feedIdOf(creatorA, slot, pos)`, which backs Pool A.
3. Pusher calls `revokePusher()` and signs a new consent for Creator B. Creator B calls `allowPushers` → `namespaceRemapping[pusher] = creatorB`. Pusher now intends to feed Pool B.
4. Creator A replays the old signature: `allowPushers(deadline, [pusher], [oldSig])` → `namespaceRemapping[pusher] = creatorA` again.
5. Every fallback push the pusher makes (intended for `feedIdOf(creatorB, slot, pos)`) now lands in `feedIdOf(creatorA, slot, pos)`.
6. Pool B's feed (`feedIdOf(creatorB, slot, pos)`) stops receiving updates → stale bid/ask prices reach Pool B's swaps.
7. Pool A receives prices the pusher intended for a different pool, which may be for a different asset pair or price scale.

Both pools are exposed to bad-price execution: Pool B trades at a stale oracle quote; Pool A may trade at a quote calibrated for a different market.

---

### Likelihood Explanation

- The creator already holds the pusher's signature (they used it to establish the original delegation).
- No special privilege is required beyond being the creator of a namespace — this is an unprivileged, permissionless call.
- The attack window equals the remaining time until the pusher's deadline, which can be arbitrarily long (the protocol imposes no cap on deadline length).
- The pusher has no on-chain way to detect or prevent the replay; `revokePusher` emits `PusherRevoked` but the creator's subsequent `allowPushers` call simply overwrites the mapping again.

---

### Recommendation

Track consumed delegation digests. Add a `mapping(bytes32 => bool) private _usedDelegations` and mark each digest as consumed on first use:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(!_usedDelegations[hash], "signature already consumed");
require(pusher == ECDSA.recover(hash, signatures[i]));
_usedDelegations[hash] = true;
```

Alternatively, add a per-pusher revocation nonce that is incremented by `revokePusher` and included in the signed digest, so any previously issued signature is automatically invalidated on revocation.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent for creatorA with a 30-day deadline.
uint256 deadline = block.timestamp + 30 days;
bytes32 digest = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(oracle), deadline, pusher, creatorA))
);
bytes memory sig = sign(PUSHER_KEY, digest);

// 2. CreatorA establishes delegation.
vm.prank(creatorA);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creatorA);

// 3. Pusher revokes and re-delegates to creatorB.
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0));

bytes memory sig2 = sign(PUSHER_KEY, digestFor(creatorB, deadline2));
vm.prank(creatorB);
oracle.allowPushers(deadline2, _arr(pusher), _arr(sig2));
assertEq(oracle.namespaceRemapping(pusher), creatorB);

// 4. CreatorA replays the OLD signature — no revert, delegation restored.
vm.prank(creatorA);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creatorA); // ← pusher's revocation undone

// 5. Pusher's next push lands in creatorA's namespace, not creatorB's.
vm.prank(pusher);
(bool ok,) = address(oracle).call(pushWord(slot, pos, price, ts));
assertTrue(ok);
// feedIdOf(creatorA, slot, pos) now has the new price — Pool A gets it.
// feedIdOf(creatorB, slot, pos) is stale — Pool B trades at old prices.
assertGt(oracle.getOracleData(oracle.feedIdOf(creatorA, slot, pos)).price, 0);
assertEq(oracle.getOracleData(oracle.feedIdOf(creatorB, slot, pos)).price, 0); // stale
``` [5](#0-4) [6](#0-5) [7](#0-6)

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
