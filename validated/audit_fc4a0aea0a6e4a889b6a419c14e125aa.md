### Title
`revokePusher` Self-Revocation Is Ineffective While the Original Signed Consent Remains Unexpired — (`File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`revokePusher()` clears `namespaceRemapping[pusher]` to `address(0)`, but `allowPushers` contains no nonce or used-signature registry. The creator can immediately replay the same EIP-191 consent signature (with a still-valid deadline) to re-establish `namespaceRemapping[pusher] = creator`. A pusher whose key is compromised cannot unilaterally exit the delegation until the original deadline expires, allowing the attacker holding the pusher key to resume writing bad prices into the creator's namespace.

---

### Finding Description

`allowPushers` signs consent as:

```
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

There is no nonce, no per-pusher revocation counter, and no "used signatures" mapping. The only replay guard is `_ensureDeadline`, which only rejects signatures whose deadline has already passed. [2](#0-1) 

`revokePusher` clears the mapping:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [3](#0-2) 

But it does not invalidate the original signed consent. The creator can call `allowPushers` again with the identical `(deadline, pusher, signature)` tuple — the signature still recovers to `pusher`, the deadline still passes `_ensureDeadline`, and `namespaceRemapping[pusher]` is written back to `creator`. The code comment acknowledges the risk but incorrectly claims the deadline alone prevents it:

> "The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it." [4](#0-3) 

The deadline prevents using an *expired* signature; it does not prevent replaying a *valid* one. The analog to `PersonalAccountRegistry` is exact: removal clears one field (`namespaceRemapping`) but leaves the authorization artifact (the signed consent) intact and reusable, so the "removed" pusher can be silently re-established.

The `fallback` push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [5](#0-4) 

Once the delegation is re-established, every subsequent fallback push from the compromised pusher key lands in the creator's namespace, overwriting live feed slots consumed by price providers and pools.

---

### Impact Explanation

A compromised pusher key can push arbitrary `(price, spread0, spread1, timestampMs)` tuples into the creator's namespace. Price providers read these values directly via `getOracleData` → `price()`, and pools execute swaps against the returned bid/ask. A manipulated price causes traders to receive more output than the oracle curve permits or forces the pool to accept less input than owed, draining LP principal. [6](#0-5) 

---

### Likelihood Explanation

Realistic trigger: a pusher signs a consent with a 30-day deadline (common operational practice). On day 5 the pusher's key is compromised; the pusher calls `revokePusher`. The creator's automated delegation-management script detects the revocation event and re-calls `allowPushers` with the cached signature to "restore" the pusher — a pattern natural for any system that monitors `PusherRevoked` events and treats them as accidental. The attacker now holds the pusher key and resumes pushing bad prices for the remaining 25 days.

---

### Recommendation

Track used consent hashes in a `mapping(bytes32 => bool) private _usedConsents` and mark each hash consumed on first use inside `allowPushers`. Additionally, `revokePusher` should write the consumed hash so the creator cannot replay it even before the deadline:

```solidity
mapping(bytes32 => bool) private _usedConsents;

// in allowPushers, after ECDSA.recover:
require(!_usedConsents[hash], ConsentAlreadyUsed());
_usedConsents[hash] = true;
```

Alternatively, add a per-pusher nonce incremented by `revokePusher` and included in the signed message, so any pre-revocation signature is automatically invalidated.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent with deadline = now + 30 days
uint256 deadline = block.timestamp + 30 days;
bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

// 2. Creator establishes delegation
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator);

// 3. Pusher's key is compromised; pusher self-revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0)); // cleared

// 4. Creator replays the SAME signature (deadline still valid)
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig)); // succeeds — no revert
assertEq(oracle.namespaceRemapping(pusher), creator);   // delegation re-established

// 5. Attacker (holding pusher key) pushes a manipulated price into creator's namespace
uint56 tsMs = uint56(block.timestamp * 1000);
uint48 badRaw = _packRaw(type(uint32).max, 0, 0); // max price
vm.prank(pusher); // attacker controls this key
(bool ok,) = address(oracle).call(_wordAt(0, 0, badRaw, tsMs));
assertTrue(ok);

// 6. Bad price is now live in creator's namespace, consumed by pools
IOffchainOracle.OracleData memory data = oracle.getOracleData(oracle.feedIdOf(creator, 0, 0));
assertEq(data.price, U64x32.decode(uint32(badRaw >> 16))); // manipulated value
``` [7](#0-6) [3](#0-2) [8](#0-7)

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

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L124-126)
```text
    function _ensureDeadline(uint256 deadline) internal view virtual {
        require(block.timestamp <= deadline, DeadlineExceeded());
    }
```
