### Title
`allowPushers` signature replay re-establishes delegation after `revokePusher()`, making pusher self-revocation ineffective — (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` accepts a pusher's EIP-191 consent signature but tracks **no nonce and no used-signature set**. After a pusher calls `revokePusher()` to zero out `namespaceRemapping[pusher]`, the creator can immediately replay the original, still-valid signature to re-establish the delegation. The code's own NatSpec comment acknowledges the concern ("an undated signature could re-establish a delegation AFTER the pusher revoked it") and claims the deadline is the fix — but the deadline only blocks replay *after* it expires, not within the live window. Within that window, `revokePusher()` is a no-op.

---

### Finding Description

**Invariant the code intends to enforce:**
A pusher who calls `revokePusher()` is permanently removed from the creator's namespace until they produce a *new* signed consent.

**Why it breaks:**

`allowPushers` checks three things and nothing more:

```solidity
// CompressedOracle.sol L192-211
function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
    _ensureDeadline(deadline);                          // (1) deadline not expired
    ...
    require(pusher != msg.sender);                      // (2) no self-remap
    bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
        keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
    );
    require(pusher == ECDSA.recover(hash, signatures[i])); // (3) valid sig
    namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

There is no fourth check: **"has this exact signature already been consumed?"** No nonce, no `usedSignatures` mapping, nothing.

`revokePusher()` only zeroes the mapping:

```solidity
// CompressedOracle.sol L238-243
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
``` [2](#0-1) 

Because the signature is not invalidated, the creator can call `allowPushers` again with the identical `(deadline, pusher, sig)` tuple and restore `namespaceRemapping[pusher] = creator` — as many times as desired until the deadline timestamp passes.

**Analog to the seed bug:**
In `VotingEscrow.merge`, the code checks `voted[_from]` (set to `false` by `abstain`) but ignores `lastVoted[_from]` (set by `reset`). Here, `allowPushers` checks the signature (valid) and the deadline (not expired) but ignores whether the pusher already revoked — the two-state system (`namespaceRemapping` zeroed by `revokePusher`, signature still live) is exactly the same incomplete-guard pattern.

---

### Impact Explanation

Once the creator replays the signature and `namespaceRemapping[pusher] = creator` is restored, the `fallback()` push path accepts arbitrary price data from the pusher's address with no further authorization:

```solidity
// CompressedOracle.sol L315-343
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
...
bool newer = timestampMs.isAfter(oldTimestampMs);
if (!newer) continue;
_writeStorage(key, bytes32(bytes32(word & ~uint256(0xff))));
``` [3](#0-2) 

The only guards on the push path are `maxTimeDrift` (timestamp not too far in the future) and monotonicity (timestamp strictly newer than stored). Neither prevents pushing an arbitrary manipulated price. Those prices are then returned verbatim by `price()` → `_price()` → `getOracleData()` to any pool that reads the feed, causing **bad-price execution**: pools swap at an attacker-controlled bid/ask, letting the attacker extract value from LPs or traders. [4](#0-3) 

---

### Likelihood Explanation

The realistic trigger is a creator who operates an automated re-delegation system (common in production oracle infrastructure where pushers are rotated or accidentally revoke). When a pusher's key is compromised:

1. The pusher calls `revokePusher()` to stop the attacker.
2. The creator's automation, unaware of the compromise, replays the stored `(deadline, pusher, sig)` tuple to "restore" the pusher.
3. The attacker (holding the compromised key) resumes pushing arbitrary prices via `fallback()`.

The code's own NatSpec comment at L186–191 explicitly names this exact scenario as the motivation for the deadline — confirming the developers considered it a real threat — but the deadline only closes the window after expiry, not within it. [5](#0-4) 

---

### Recommendation

Track consumed signatures with a `mapping(bytes32 => bool) private _usedSignatures` and mark each hash as used on first acceptance:

```diff
+mapping(bytes32 => bool) private _usedSignatures;

 function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
     _ensureDeadline(deadline);
     ...
     bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
         keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
     );
     require(pusher == ECDSA.recover(hash, signatures[i]));
+    require(!_usedSignatures[hash], SignatureAlreadyUsed());
+    _usedSignatures[hash] = true;
     namespaceRemapping[pusher] = msg.sender;
```

Alternatively, include a per-pusher nonce in the signed payload so each consent is single-use by construction.

---

### Proof of Concept

```
Step 1 — Setup:
  Pusher P signs: keccak256(abi.encode(chainid, oracle, deadline=T+365days, P, C))
  Creator C calls allowPushers(T+365days, [P], [sig])
  → namespaceRemapping[P] = C  ✓

Step 2 — Pusher key is compromised; pusher self-revokes:
  P calls revokePusher()
  → namespaceRemapping[P] = address(0)  ✓

Step 3 — Creator's automation replays the original call (same sig, same deadline):
  C calls allowPushers(T+365days, [P], [sig])   // identical calldata
  → _ensureDeadline: T+365days > block.timestamp  ✓ (passes)
  → ECDSA.recover: returns P                      ✓ (passes — sig is still valid)
  → namespaceRemapping[P] = C  (restored)

Step 4 — Attacker (holding compromised P key) pushes manipulated price:
  Attacker calls oracle.fallback(slotWord)
  → namespaceRemapping[attacker_addr] = C  (resolved)
  → timestampMs > stored timestamp           ✓ (passes)
  → _writeStorage writes attacker price into C's slot

Step 5 — Pool reads price:
  pool.swap() → priceProvider.getBidAndAskPrice() → oracle.price(feedId, pool)
  → getOracleData() returns attacker-controlled mid/spread
  → pool executes swap at manipulated bid/ask → LP funds drained
```

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L192-211)
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-343)
```text
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
```
