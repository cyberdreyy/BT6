### Title
`revokePusher` is silently nullified while the original consent signature's deadline is still valid — creator can replay the same EIP-191 signature to restore a revoked pusher's write access to the oracle namespace - (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` verifies a pusher's EIP-191 consent signature but never marks that signature as consumed. A creator who retains a still-valid (deadline not yet expired) consent signature can call `allowPushers` a second time with the identical `(deadline, pusher, creator)` tuple and restore a delegation the pusher already cancelled via `revokePusher`. The pusher's only on-chain safety exit is therefore ineffective for the entire remaining lifetime of the original deadline.

---

### Finding Description

The `allowPushers` function in `CompressedOracleV1` verifies a pusher's EIP-191 consent and writes `namespaceRemapping[pusher] = msg.sender`:

```solidity
// CompressedOracle.sol L192-211
function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
    _ensureDeadline(deadline);
    ...
    bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
        keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
    );
    require(pusher == ECDSA.recover(hash, signatures[i]));
    namespaceRemapping[pusher] = msg.sender;   // ← write
    ...
}
``` [1](#0-0) 

`revokePusher` clears the mapping:

```solidity
// CompressedOracle.sol L238-243
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);   // ← clear
    emit PusherRevoked(msg.sender, creator);
}
``` [2](#0-1) 

There is **no nonce, no "signature consumed" flag, and no check that the pusher is not already revoked**. The `grep_search` across all production contracts confirms zero occurrences of any `usedSignature`, `signatureUsed`, `nonce`, or `consumed` guard in the oracle contracts. Because the same `(block.chainid, address(this), deadline, pusher, msg.sender)` tuple produces the same hash, the creator can replay the original consent signature at any time before `deadline` to re-establish the delegation.

The code's own NatDoc comment explicitly acknowledges this risk but misidentifies the deadline as a complete fix:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [3](#0-2) 

The deadline only prevents re-establishment **after** it expires. While the deadline is still valid, the creator can replay the signature immediately after the pusher revokes, restoring full write authority.

The `generate_scanned_questions.py` audit target file explicitly flags this surface:

> *"Delegation clean-up is a public surface because any stale remapping after revoke is effectively latent write authority. Exercise revoke/remove interleavings and assert no later public push can still write into a namespace that should have been detached."* [4](#0-3) 

---

### Impact Explanation

Once the delegation is restored, the attacker (holding the compromised pusher key) can call the `fallback` push path and write arbitrary packed slot words into the creator's namespace:

```solidity
// CompressedOracle.sol L311-344 — fallback push path
fallback() override external {
    address creator = namespaceRemapping[msg.sender];   // ← restored mapping
    if (creator == address(0)) creator = msg.sender;
    ...
    _writeStorage(key, bytes32(bytes32(word & ~uint256(0xff))));
}
``` [5](#0-4) 

The corrupted slot value is then decoded by `getOracleData` and returned by `price(feedId, pool)`:

```solidity
// CompressedOracle.sol L171-178
function _price(bytes32 feedId) internal view
    returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
{
    OracleData memory data = getOracleData(feedId);
    return (uint256(data.price), uint256(data.spread0), data.spread1, data.timestampMs.toSeconds());
}
``` [6](#0-5) 

This bad price flows directly into `AnchoredPriceProvider._readLeg` → `_computeBidAsk` → `getBidAndAskPrice()` → pool swap, satisfying the **bad-price execution** impact gate. The `CompressedOracleV1` has no `inSwap` binding or registration gate (it is explicitly open/permissionless), so the corrupted price reaches any pool that reads through it without additional filtering. [7](#0-6) 

---

### Likelihood Explanation

The scenario requires two conditions:

1. A pusher's private key is compromised.
2. The creator calls `allowPushers` with the original signature after the pusher revokes — either because the creator is unaware of the compromise, or because the creator is colluding.

Condition 2 is realistic: the creator may have stored the original consent signature for operational re-use (e.g., to re-onboard a pusher after a temporary outage) and replay it without realising the pusher's key was compromised. The deadline is typically set far in the future (the test suite uses `block.timestamp + 1 days`, but production deployments may use weeks or months). [8](#0-7) 

---

### Recommendation

Track each consent signature as consumed after first use. The minimal fix is a `mapping(bytes32 => bool) private _usedConsents` keyed on the signature hash, checked and set inside `allowPushers`:

```solidity
mapping(bytes32 => bool) private _usedConsents;

function allowPushers(...) external {
    _ensureDeadline(deadline);
    ...
    bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
        keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
    );
    require(!_usedConsents[hash], "consent already used");
    require(pusher == ECDSA.recover(hash, signatures[i]));
    _usedConsents[hash] = true;
    namespaceRemapping[pusher] = msg.sender;
    ...
}
```

Alternatively, include a per-pusher nonce in the signed message so each consent is single-use by construction.

---

### Proof of Concept

```
1. Pusher signs consent:
   sig = sign(keccak256(abi.encode(chainid, oracle, deadline=T+365days, pusher, creator)))

2. Creator calls allowPushers(T+365days, [pusher], [sig])
   → namespaceRemapping[pusher] = creator  ✓

3. Pusher's key is compromised; attacker pushes bad prices via fallback.

4. Pusher calls revokePusher()
   → namespaceRemapping[pusher] = address(0)  ✓ (revocation succeeds)

5. Creator calls allowPushers(T+365days, [pusher], [sig])  ← SAME signature, deadline still valid
   → _ensureDeadline passes (T+365days > block.timestamp)
   → ECDSA.recover returns pusher  ← same hash, same sig
   → namespaceRemapping[pusher] = creator  ← delegation RESTORED

6. Attacker (holding compromised pusher key) calls fallback with crafted slot word
   → bad price written into creator's namespace
   → CompressedOracleV1.price() returns corrupted mid/spread
   → AnchoredPriceProvider._readLeg() passes staleness/guard checks (attacker sets fresh timestamp)
   → getBidAndAskPrice() returns bad bid/ask to pool swap
``` [9](#0-8) [2](#0-1) [5](#0-4)

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L171-178)
```text
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

**File:** generate_scanned_questions.py (L1010-1016)
```python
            short="compressed self-revocation and removal",
            file_function="smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::{revokePusher,removePushers}",
            entrypoint="smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::{revokePusher,removePushers}",
            call_path="public revoke/remove -> namespaceRemapping clear -> later fallback pushes revert to creator or self namespace",
            values="the namespace actually revoked, any surviving stale delegation, and whether later pushes still land in the old namespace",
            control_hint="Delegation clean-up is a public surface because any stale remapping after revoke is effectively latent write authority.",
            validation_focus="Exercise revoke/remove interleavings and assert no later public push can still write into a namespace that should have been detached.",
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

**File:** smart-contracts-poc/test/oracles/compressed/CompressedOracle.t.sol (L339-356)
```text
    function testAllowPushersDelegatesNamespace() public {
        uint256 deadline = block.timestamp + 1 days;
        _allowPusher(deadline);
        assertEq(oracle.namespaceRemapping(pusher), creator, "pusher should map to creator");

        // delegated push lands in the CREATOR namespace, not the pusher's own
        uint56 tsMs = uint56(block.timestamp * 1000);
        uint48 raw = _packRaw(900_000, 5, 0);
        vm.prank(pusher);
        (bool ok,) = address(oracle).call(_wordAt(2, 3, raw, tsMs));
        assertTrue(ok, "delegated push failed");

        IOffchainOracle.OracleData memory data = oracle.getOracleData(oracle.feedIdOf(creator, 2, 3));
        assertEq(data.price, U64x32.decode(uint32(raw >> 16)), "delegated push should land in creator namespace");

        IOffchainOracle.OracleData memory own = oracle.getOracleData(oracle.feedIdOf(pusher, 2, 3));
        assertEq(own.price, 0, "pusher's own namespace must stay empty");
    }
```
