### Title
`revokePusher()` Self-Revocation Is Ineffective Within the Deadline Window Due to Consent-Signature Replay in `allowPushers` - (File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol)

---

### Summary

`allowPushers` accepts a pusher's EIP-191 consent signature and records `namespaceRemapping[pusher] = creator`. There is no used-signature registry and no nonce. After a pusher calls `revokePusher()` to clear the mapping, the creator can immediately call `allowPushers` again with the **identical signature** (deadline still in the future) to re-establish the delegation. The pusher's self-revocation is silently undone, and every subsequent fallback push from the pusher continues to land in the creator's namespace rather than the pusher's own.

---

### Finding Description

`allowPushers` performs three checks before writing `namespaceRemapping[pusher] = msg.sender`:

1. `_ensureDeadline(deadline)` — deadline has not passed
2. Signature recovers to `pusher` over `(chainid, address(this), deadline, pusher, msg.sender)`
3. `pusher != msg.sender` [1](#0-0) 

None of these checks prevent the **same signature from being submitted a second time**. There is no nonce, no `usedSignatures` mapping, and no check that the pusher has not already revoked.

`revokePusher()` clears the mapping: [2](#0-1) 

But because `allowPushers` is stateless with respect to prior revocations, the creator can call it again in the same transaction or the next block with the original `(deadline, pusher, sig)` tuple, restoring `namespaceRemapping[pusher] = creator`. This can be repeated arbitrarily until the deadline expires.

The code comment on `allowPushers` explicitly acknowledges the replay risk and claims the deadline solves it:

> *"the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [3](#0-2) 

The comment is incorrect: the deadline only prevents replay **after** the deadline expires. Within the deadline window — which can be arbitrarily long — the same signature is replayable unlimited times. This is the direct analog to the Migration.sol bug where a failed proposal can be committed again within the 7-day window.

The `fallback` push path resolves the namespace at call time: [4](#0-3) 

So every push the pusher makes after a re-established delegation lands in the creator's namespace, not the pusher's own. The pusher's own namespace stays at price 0 / timestamp 0 (stale), while the creator's namespace receives the pusher's price updates.

---

### Impact Explanation

An automated price-pushing bot that calls `revokePusher()` to stop feeding a creator's namespace will continue to feed it without knowing the delegation was re-established. The creator's pool — backed by a `PriceProvider` reading the creator's `CompressedOracle` feeds — continues to receive live prices from the pusher against the pusher's explicit intent. If the creator's pool or extension is malicious, it can exploit these prices to harm LPs (e.g., by executing swaps at rates the pusher no longer consents to provide). Additionally, the pusher's own namespace stays empty, causing any pool that the pusher intended to feed after revocation to stall (`refTime = 0`, rejected as stale by every provider).

The broken invariant is: `revokePusher()` is documented and designed to give the pusher unilateral control over their delegation, but within the deadline window the creator can undo it in a single transaction.

---

### Likelihood Explanation

The trigger is a valid creator who holds a non-expired pusher consent signature — a normal operational state. No special privilege is required beyond being the creator who originally called `allowPushers`. The pusher's `revokePusher()` transaction is public and observable on-chain, so a creator can front-run or immediately follow it with a replay call. Likelihood is medium: it requires a creator who is motivated to retain a pusher's feed against the pusher's will, but the mechanism is trivially executable.

---

### Recommendation

Track used consent signatures to prevent replay. The simplest fix is a `mapping(bytes32 => bool) public usedConsentSignatures` keyed on the signature hash, set to `true` on first use and checked at the start of `allowPushers`. Alternatively, include a per-pusher nonce in the signed message and increment it on each successful delegation or revocation, so a revoked pusher must produce a fresh signature with the new nonce to be re-delegated.

```solidity
// In allowPushers, after recovering the signer:
bytes32 sigHash = keccak256(signatures[i]);
require(!usedConsentSignatures[sigHash], "signature already used");
usedConsentSignatures[sigHash] = true;
namespaceRemapping[pusher] = msg.sender;
```

---

### Proof of Concept

```solidity
// 1. Pusher signs consent with deadline = block.timestamp + 30 days
bytes memory sig = pusher.sign(keccak256(abi.encode(
    block.chainid, address(oracle), deadline, pusherAddr, creatorAddr
)));

// 2. Creator establishes delegation
vm.prank(creator);
oracle.allowPushers(deadline, [pusherAddr], [sig]);
// namespaceRemapping[pusher] == creator ✓

// 3. Pusher self-revokes
vm.prank(pusher);
oracle.revokePusher();
// namespaceRemapping[pusher] == address(0) ✓

// 4. Creator replays the SAME signature — no revert, delegation restored
vm.prank(creator);
oracle.allowPushers(deadline, [pusherAddr], [sig]);
// namespaceRemapping[pusher] == creator again ✓

// 5. Pusher's next fallback push lands in creator's namespace, not pusher's own
vm.prank(pusher);
(bool ok,) = address(oracle).call(slotWord);
assertTrue(ok);
// oracle.getOracleData(feedIdOf(creator, slotId, pos)).price == pusherPrice ✓
// oracle.getOracleData(feedIdOf(pusher,   slotId, pos)).price == 0          ✓
``` [5](#0-4) [2](#0-1) [6](#0-5)

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
