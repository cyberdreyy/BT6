### Title
Contract-Pusher Namespace Hijack Silently Redirects Price Feeds, Causing Stale-Price DoS on Pools — (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.allowContractPushers` does not check whether a contract pusher is already delegated to another creator. Any creator that the pusher contract authorizes can unconditionally overwrite the existing `namespaceRemapping` entry, silently redirecting all future price pushes to their own namespace. The original creator's feeds stop being updated, become stale, and every pool anchored to those feeds halts with `FeedStalled`.

---

### Finding Description

`allowContractPushers` establishes delegation by calling `pusher.isPusher(msg.sender)` and then unconditionally writing `namespaceRemapping[pusher] = msg.sender`: [1](#0-0) 

There is no guard of the form `require(namespaceRemapping[pusher] == address(0) || namespaceRemapping[pusher] == msg.sender)`. If a contract pusher `P` is designed to serve multiple creators — a realistic pattern for a shared price-aggregator service — any creator that `P.isPusher(creator)` returns `true` for can call `allowContractPushers([P])` and overwrite the existing delegation.

The fallback push path resolves the namespace as: [2](#0-1) 

After the hijack, every push from `P` lands in the attacker's namespace. The victim creator's feeds receive no further updates.

The staleness check in `AnchoredPriceProvider._readLeg` then fires: [3](#0-2) 

`_readLeg` returns `ok = false`, `_getBidAndAskPrice` returns the `(0, type(uint128).max)` sentinel, and `getBidAndAskPrice` converts that to a `FeedStalled` revert: [4](#0-3) 

Every swap through the victim's pool fails. Additionally, `removePushers` checks `namespaceRemapping[pusher] == msg.sender` before allowing removal: [5](#0-4) 

After the hijack, `namespaceRemapping[P] = creatorB`, so `creatorA` cannot even call `removePushers` to clean up — the revert path `InvalidManager` fires. The victim loses all management authority over the pusher they originally delegated.

---

### Impact Explanation

The victim creator's pool becomes permanently unusable for swaps until they deploy a new pusher contract and re-establish delegation. This is a broken core pool functionality: an unusable swap flow. The stale-price path is explicitly listed as a contest-relevant impact ("Bad-price execution: stale… bid/ask quote reaches a pool swap" and "Broken core pool functionality… unusable… swap… flows").

---

### Likelihood Explanation

Low-to-Medium. The attack requires a contract pusher that returns `true` for multiple creators — a realistic pattern for any shared price-aggregator or keeper service. The attacker must be one of the creators the pusher contract already authorizes; no external privilege is needed beyond that. The `allowContractPushers` call itself is fully permissionless.

---

### Recommendation

Add an existence check before overwriting the delegation in `allowContractPushers`:

```solidity
function allowContractPushers(address[] calldata pushers) external {
    uint256 l = pushers.length;
    for (uint256 i; i < l; i++) {
        address pusher = pushers[i];
        if (pusher == msg.sender) revert NoSelfRemapping();

        // NEW: prevent hijacking an existing delegation
        address existing = namespaceRemapping[pusher];
        require(existing == address(0) || existing == msg.sender, "pusher already delegated");

        (bool ok, bytes memory res) = pusher.staticcall(
            abi.encodeWithSignature("isPusher(address)", msg.sender)
        );
        require(ok);
        require(abi.decode(res, (bool)));

        namespaceRemapping[pusher] = msg.sender;
        emit PusherAuthorized(pusher, msg.sender);
    }
}
```

The same guard should be applied to `allowPushers` for consistency.

---

### Proof of Concept

1. `creatorA` calls `allowContractPushers([sharedPusher])` → `namespaceRemapping[sharedPusher] = creatorA`. [6](#0-5) 
2. `sharedPusher` regularly pushes prices into `creatorA`'s namespace; `creatorA`'s pool reads from `feedIdOf(creatorA, slotIndex, positionIndex)`. [7](#0-6) 
3. `creatorB` calls `allowContractPushers([sharedPusher])` — `sharedPusher.isPusher(creatorB)` returns `true` (shared aggregator) → `namespaceRemapping[sharedPusher] = creatorB`. No revert occurs.
4. `sharedPusher`'s next push lands in `creatorB`'s namespace via the fallback namespace resolution. [8](#0-7) 
5. `creatorA`'s feeds receive no further updates. After `MAX_REF_STALENESS` seconds, `_isStale` returns `true`. [9](#0-8) 
6. `creatorA`'s pool's `getBidAndAskPrice()` reverts with `FeedStalled`; all swaps fail.
7. `creatorA` attempts `removePushers([sharedPusher])` — reverts with `InvalidManager` because `namespaceRemapping[sharedPusher] == creatorB`. The victim has no recovery path without deploying a new pusher. [5](#0-4)

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L49-53)
```text
    function feedIdOf(address creator, uint8 slotIndex, uint8 positionIndex) public view returns (bytes32) {
        return bytes32(
            uint256(uint160(creator)) << 96 | block.chainid << 16 | uint256(slotIndex) << 8 | positionIndex
        );
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L217-233)
```text
    function allowContractPushers(address[] calldata pushers) external {
        uint256 l = pushers.length;
        for (uint256 i; i < l; i++) {
            address pusher = pushers[i];

            if (pusher == msg.sender) {
                revert NoSelfRemapping();
            }

            (bool ok, bytes memory res) = pusher.staticcall(abi.encodeWithSignature("isPusher(address)", msg.sender));
            require(ok);
            bool allowed = abi.decode(res, (bool));
            require(allowed);

            namespaceRemapping[pusher] = msg.sender;
            emit PusherAuthorized(pusher, msg.sender);
        }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L253-258)
```text
            if (namespaceRemapping[pusher] == msg.sender) {
                namespaceRemapping[pusher] = address(0);
                emit PusherRevoked(pusher, msg.sender);
            } else {
                revert InvalidManager(msg.sender);
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

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L214-217)
```text
    function getBidAndAskPrice() external override returns (uint128 bid, uint128 ask) {
        (bid, ask) = _getBidAndAskPrice();
        if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
    }
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

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L282-283)
```text
        // Stale reference → not ok. Clamping to a stale anchor is the one false-safety case.
        if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);
```
