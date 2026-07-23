### Title
`updateBySignature` Missing Deadline Parameter Allows Indefinite Replay of Signed Slot Words, Enabling Stale Price Injection - (File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol)

---

### Summary

The `updateBySignature` function in `CompressedOracleV1` is missing the `deadline` parameter that is explicitly required by the protocol's own documentation and ABI registry. Any signed slot word obtained by a relayer can be submitted at any future time with no expiry, allowing a stale price to be written into the oracle and subsequently consumed by pools.

---

### Finding Description

The protocol's top-level documentation and the deployed ABI both specify `updateBySignature` as:

```
updateBySignature(feedCreator, deadline, newSlotValue, signature)
```

with the signature covering `keccak256(abi.encode(chainid, oracleAddress, feedCreator, deadline, newSlotValue))` and the required guard: *"deadline must be in the future (DeadlineExceeded otherwise)"*.

The actual on-chain implementation is:

```solidity
function updateBySignature(address feedCreator, uint256 newSlotValue, bytes calldata signature)
    external override returns (bool)
{
    // ...
    bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
        keccak256(abi.encode(block.chainid, address(this), feedCreator, newSlotValue))
    );
    require(feedCreator == ECDSA.recover(hash, signature));
    _writeStorage(key, bytes32(newSlotValue & ~uint256(0xff)));
    return true;
}
``` [1](#0-0) 

There is no `deadline` parameter, no `_ensureDeadline` call, and no deadline field in the signed hash. The `_ensureDeadline` helper exists in `OracleBase` and is already used correctly by `allowPushers`, but it is entirely absent from `updateBySignature`. [2](#0-1) 

The discrepancy is confirmed by three independent sources:

1. **Top-level docs** (`smart-contracts-poc/docs/en/oracle-packet-structure.md`, lines 37 and 45) explicitly list `deadline` as a required parameter and guard. [3](#0-2) 

2. **Deployed ABI** (`smart-contracts-poc/contract-registry/versions/registry.json`, lines 3601–3632) lists `deadline` as the second input of `updateBySignature`. [4](#0-3) 

3. **`allowPushers`** — the sibling delegation function — correctly uses `_ensureDeadline` and includes the deadline in its signed hash, demonstrating the intended pattern. [5](#0-4) 

The only replay protection in the current implementation is the per-slot monotonicity check (`timestampMs.isAfter(oldTimestampMs)`). This is insufficient: a signed slot word with timestamp T remains valid and submittable by any party indefinitely, as long as the stored slot timestamp is less than T.

---

### Impact Explanation

**Attack path:**

1. A feed creator signs a slot word at time T encoding price P and hands it to a relayer.
2. Market price moves to P′. The creator pushes a fresh update (timestamp T₂ > T, price P′) for the affected slot, making the old signature useless for that slot.
3. However, the creator also has other slots (or the same slot on a chain where it was never pushed, stored timestamp = 0). The old signed slot word (T, P) remains valid for any slot whose stored timestamp is < T.
4. The relayer (or any party who obtained the signature) submits the old slot word. Because T ≤ block.timestamp + drift (the only on-chain check), the write succeeds and the oracle stores stale price P with timestamp T.
5. A price provider reading the compressed oracle calls `price(feedId, pool)` and receives the stale mid/spread. [6](#0-5) 

6. The `AnchoredPriceProvider` (or any provider backed by the compressed oracle) checks staleness via `_isStale(refTime, block.timestamp, MAX_REF_STALENESS)`. If the stale timestamp T is within `MAX_REF_STALENESS` seconds of the current block, the stale price passes the staleness gate and is used to compute bid/ask for the pool swap. [7](#0-6) 

7. The pool executes the swap at the wrong (stale) price. Traders receive more output than the true oracle price permits (bad-price execution), or LPs receive less input than owed.

---

### Likelihood Explanation

- `updateBySignature` is a **public, permissionless** function — any address can call it with a valid creator signature.
- Signed slot words are routinely produced by off-chain infrastructure and shared with relayers. Any relayer, MEV searcher, or mempool observer who captures a signed slot word can replay it at will.
- The creator has no on-chain mechanism to invalidate an already-signed slot word short of pushing a newer update to every affected slot — which may not be possible if the creator's key is unavailable or the slot was never previously pushed (stored timestamp = 0).
- The `MAX_TIME_DRIFT` (60 seconds in production config) only prevents future-dated timestamps; it places no lower bound on how old the slot word's timestamp can be relative to the current block. [8](#0-7) 

---

### Recommendation

Add a `deadline` parameter to `updateBySignature`, include it in the signed hash, and call `_ensureDeadline(deadline)` before the signature recovery — exactly mirroring the pattern already used in `allowPushers`:

```solidity
function updateBySignature(
    address feedCreator,
    uint256 deadline,          // ← add
    uint256 newSlotValue,
    bytes calldata signature
) external override returns (bool) {
    _ensureDeadline(deadline); // ← add

    // ...existing timestamp and monotonicity checks...

    bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
        keccak256(abi.encode(block.chainid, address(this), feedCreator, deadline, newSlotValue))
        //                                                              ^^^^^^^^ add
    );
    require(feedCreator == ECDSA.recover(hash, signature));
    // ...
}
```

This matches the interface documented in `smart-contracts-poc/docs/en/oracle-packet-structure.md` and the deployed ABI in `registry.json`, and gives creators a finite window after which a signed slot word is permanently invalid.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";
import {MessageHashUtils} from "@openzeppelin/contracts/utils/cryptography/MessageHashUtils.sol";
import {CompressedOracleV1} from "contracts/oracles/compressed/CompressedOracle.sol";
import {IOffchainOracle} from "contracts/interfaces/IOffchainOracle.sol";
import {U64x32} from "contracts/oracles/utils/U64x32.sol";

contract MissingDeadlinePoC is Test {
    CompressedOracleV1 oracle;
    uint256 constant CREATOR_KEY = 0xC0FFEE;
    address creator;

    function setUp() public {
        oracle = new CompressedOracleV1(address(this), 60);
        creator = vm.addr(CREATOR_KEY);
        vm.warp(1_700_000_000);
    }

    function test_staleSignedSlotWordReplayedAfterIntendedExpiry() public {
        // 1. Creator signs a slot word at T=now with price P=1_000_000
        uint56 tsMs = uint56(block.timestamp * 1000);
        uint48 raw = (uint48(1_000_000) << 16) | (uint48(3) << 8) | uint48(3);
        uint256 slotValue = (uint256(tsMs) << 8) | uint256(5); // slotId=5
        slotValue |= uint256(raw) << 208;

        bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
            keccak256(abi.encode(block.chainid, address(oracle), creator, slotValue))
        );
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(CREATOR_KEY, hash);
        bytes memory sig = abi.encodePacked(r, s, v);

        // 2. Time passes — creator intended this signature to expire after 1 minute
        vm.warp(block.timestamp + 1 hours);

        // 3. Relayer submits the 1-hour-old signed slot word — NO deadline check, succeeds
        bool updated = oracle.updateBySignature(creator, slotValue, sig);
        assertTrue(updated, "stale signed slot word accepted — no deadline check");

        // 4. Oracle now stores a 1-hour-old price
        bytes32 feedId = oracle.feedIdOf(creator, 5, 0);
        IOffchainOracle.OracleData memory data = oracle.getOracleData(feedId);
        assertEq(data.price, U64x32.decode(uint32(raw >> 16)));
        // refTime is 1 hour in the past — stale price now in oracle storage
        assertEq(data.timestampMs.toSeconds(), block.timestamp - 1 hours);
    }
}
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L271-303)
```text
    function updateBySignature(address feedCreator, uint256 newSlotValue, bytes calldata signature)
        external
        override
        returns (bool)
    {
        require(feedCreator != address(0), InvalidNamespace());

        uint256 namespace;
        assembly ("memory-safe") {
            namespace := shl(96, feedCreator) // [creator:20][zeros:12]
        }

        uint8 slotId = uint8(newSlotValue); // LSB
        TimeMs timestampMs = toTimeMs(newSlotValue >> 8 & X56);
        timestampMs.revertIfAfterBlockTimeWithDrift(MAX_TIME_DRIFT);
        bytes32 key = bytes32(namespace | uint256(slotId));
        uint256 old = uint256(_loadStorage(key));
        TimeMs oldTimestampMs = toTimeMs(old >> 8 & X56);

        bool newer = timestampMs.isAfter(oldTimestampMs);
        if (!newer) {
            return false;
        }

        bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
            keccak256(abi.encode(block.chainid, address(this), feedCreator, newSlotValue))
        );
        require(feedCreator == ECDSA.recover(hash, signature));

        _writeStorage(key, bytes32(newSlotValue & ~uint256(0xff)));

        return true;
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L124-126)
```text
    function _ensureDeadline(uint256 deadline) internal view virtual {
        require(block.timestamp <= deadline, DeadlineExceeded());
    }
```

**File:** smart-contracts-poc/docs/en/oracle-packet-structure.md (L37-47)
```markdown
`updateBySignature(feedCreator, deadline, newSlotValue, signature)` expects `newSlotValue` to be a single slot word (same layout), signed by `feedCreator` over:

```text
keccak256(abi.encode(chainid, oracleAddress, feedCreator, deadline, newSlotValue))
```

## Required Guards

- `deadline` must be in the future (`DeadlineExceeded` otherwise).
- `timestamp` must not be in the future (`FutureTimestamp` otherwise).
- `timestamp` must be strictly increasing per slot (older updates are ignored).
```

**File:** smart-contracts-poc/contract-registry/versions/registry.json (L3601-3632)
```json
              "name": "updateBySignature",
              "inputs": [
                {
                  "name": "feedCreator",
                  "type": "address",
                  "internalType": "address"
                },
                {
                  "name": "deadline",
                  "type": "uint256",
                  "internalType": "uint256"
                },
                {
                  "name": "newSlotValue",
                  "type": "uint256",
                  "internalType": "uint256"
                },
                {
                  "name": "signature",
                  "type": "bytes",
                  "internalType": "bytes"
                }
              ],
              "outputs": [
                {
                  "name": "",
                  "type": "bool",
                  "internalType": "bool"
                }
              ],
              "stateMutability": "nonpayable"
            },
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L222-230)
```text
    function _isStale(
        uint256 refTime,
        uint256 nowTs,
        uint256 maxDelta
    ) internal pure returns (bool) {
        if (refTime == 0) return true;
        if (refTime > nowTs) return true;
        return (nowTs - refTime) > maxDelta;
    }
```

**File:** smart-contracts-poc/contracts/oracles/utils/TimeMs.sol (L28-30)
```text
function revertIfAfterBlockTimeWithDrift(TimeMs t0, uint256 drift) view {
    require(t0.toSeconds() <= block.timestamp + drift, FutureTimestamp());
}
```
