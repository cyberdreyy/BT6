### Title
Pusher Delegation Replay in `allowPushers` Nullifies `revokePusher` Within Deadline Window, Enabling Bad-Price Injection into Pools — (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

The `allowPushers` function in `CompressedOracle` uses a deadline-only EIP-191 consent signature with **no nonce**. After a pusher calls `revokePusher()` to clear their delegation, the creator can immediately replay the identical signature (while the deadline is still valid) to re-establish `namespaceRemapping[pusher] = creator`. The revocation invariant is broken: a pusher who self-revokes cannot prevent re-delegation until the deadline expires. If the pusher's key is compromised, an attacker can push arbitrary prices into the creator's namespace, which flows through the permissionless `CompressedOracle.price()` path into any pool backed by an `AnchoredPriceProvider` reading that feed.

---

### Finding Description

`allowPushers` verifies the pusher's EIP-191 consent over the tuple:

```
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

There is no nonce, no per-pusher revocation flag, and no consumed-signature registry. The only replay barrier is `_ensureDeadline(deadline)`, which only rejects calls after the deadline — not repeated calls before it.

`revokePusher()` clears the mapping:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [2](#0-1) 

But because the original signature is still cryptographically valid (same `chainid`, same `address(this)`, same `deadline`, same `pusher`, same `creator`), the creator can call `allowPushers` again with the exact same arguments and signature to write `namespaceRemapping[pusher] = creator` again. The code's own comment acknowledges the design intent but the fix is incomplete:

> *"an undated signature could re-establish a delegation AFTER the pusher revoked it"*

The deadline prevents re-establishment **after** expiry, but not **before** — the window between revocation and deadline expiry is fully exploitable.

The fallback push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [3](#0-2) 

So any push made after the re-delegation lands in the creator's namespace, overwriting legitimate feed data.

The `CompressedOracle.price()` function is **permissionless** — it performs no registration or blacklist check:

```solidity
function price(bytes32 feedId, address /* pool */) external view
    returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
{
    return _price(feedId);
}
``` [4](#0-3) 

Any price written into the creator's namespace is immediately readable by any `AnchoredPriceProvider` bound to that feed, which then delivers it to pool swaps via `getBidAndAskPrice()`. [5](#0-4) 

---

### Impact Explanation

A compromised pusher key that has been self-revoked can be re-delegated by the creator (knowingly or via an automated re-delegation script). The attacker then pushes a fresh, in-guard-range price into the creator's namespace. Because `CompressedOracle.price()` is permissionless, the `AnchoredPriceProvider` reads the manipulated mid price, computes a bid/ask band around it, and delivers it to the pool's `swap()` call. The pool executes at the attacker-controlled price, causing direct loss of LP principal or swap conservation failure (trader receives more than the true oracle price permits).

---

### Likelihood Explanation

- The pusher must have signed a consent with a non-trivial deadline (common for operational convenience).
- The creator must call `allowPushers` a second time after revocation — this can happen via an automated keeper or an unaware operator.
- The pusher's key must be compromised in the window between revocation and deadline expiry.
- No on-chain guard prevents the replay; it is a single public function call with a still-valid signature.

Likelihood is **medium**: the conditions are realistic in production deployments where pushers rotate keys and creators run automated delegation management.

---

### Recommendation

Add a per-pusher nonce or a consumed-signature registry to `allowPushers`:

```solidity
mapping(address => mapping(address => uint256)) public pusherNonce; // pusher => creator => nonce

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender,
        pusherNonce[pusher][msg.sender]++   // <-- nonce
    ))
);
```

Alternatively, track revoked signatures in a `mapping(bytes32 => bool) usedConsents` and mark each accepted signature as consumed. Either approach ensures that `revokePusher()` permanently invalidates the outstanding consent, matching the documented security intent.

---

### Proof of Concept

1. Pusher signs consent for creator with `deadline = block.timestamp + 30 days`:
   ```
   sig = sign(keccak256(abi.encode(chainid, oracle, deadline, pusher, creator)))
   ```
2. Creator calls `allowPushers(deadline, [pusher], [sig])` → `namespaceRemapping[pusher] = creator`. ✓
3. Pusher's private key is compromised; pusher calls `revokePusher()` → `namespaceRemapping[pusher] = address(0)`. ✓
4. Creator (or automated keeper) calls `allowPushers(deadline, [pusher], [sig])` again — **same signature, deadline still valid** → `namespaceRemapping[pusher] = creator` restored. ✓
5. Attacker (holding compromised key) calls the oracle's `fallback` with a crafted 32-byte word encoding a manipulated price and a fresh timestamp:
   ```
   word = (manipulatedRaw << 64) | (freshTsMs << 8) | slotId
   oracle.call(abi.encodePacked(word))
   ```
   The fallback resolves `namespaceRemapping[attacker_key] = creator` and writes the bad price into `creator`'s slot. ✓
6. `AnchoredPriceProvider._readLeg(baseFeedId)` calls `CompressedOracle.price(feedId, pool)` (permissionless), receives the manipulated mid, passes staleness and guard checks (fresh timestamp, in-range price), and returns a bad bid/ask to the pool. ✓
7. Pool executes `swap()` at the attacker-controlled price → LP assets drained or trader receives excess output. [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L258-272)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        (uint256 mid, uint256 spreadBps, , bool ok) = _readLeg(baseFeedId);
        if (!ok) return (0, type(uint128).max);

        bytes32 _quote = quoteFeedId;
        if (_quote != bytes32(0)) {
            (uint256 mid2, uint256 spreadBps2, , bool ok2) = _readLeg(_quote);
            if (!ok2 || mid2 == 0) return (0, type(uint128).max);
            // Synthetic ratio (8-decimal): mid1 / mid2. Relative uncertainties of a ratio add.
            mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);
            spreadBps += spreadBps2;
        }

        return _computeBidAsk(mid, spreadBps);
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
