### Title
Stale Contract-Pusher Delegation Persists After `isPusher` Consent Revocation, Enabling Unauthorized Price Injection into Creator Namespace - (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary

`allowContractPushers` validates consent via a one-time live `isPusher(creator)` staticcall at delegation time, but the resulting `namespaceRemapping` entry persists indefinitely. If the pusher contract later changes its `isPusher` return value to `false` (revoking consent), the creator may believe the delegation is revoked, but the pusher retains write authority over the creator's namespace and can inject arbitrary prices that downstream `AnchoredPriceProvider` consumers treat as valid oracle data.

### Finding Description

`allowContractPushers` (lines 217–234) establishes a delegation by:
1. Calling `pusher.isPusher(msg.sender)` via staticcall
2. If `true`, setting `namespaceRemapping[pusher] = msg.sender`

The code comment states: *"consent is proven by a LIVE `isPusher(creator)` staticcall instead of a signature, so there is nothing to replay and no deadline is needed."* This implies the live check is the ongoing consent mechanism. However, the live check is performed **only once** at delegation time. [1](#0-0) 

After delegation, the `fallback()` push path reads `namespaceRemapping[msg.sender]` **without re-checking `isPusher`**: [2](#0-1) 

If the pusher contract's `isPusher` return value changes to `false` after delegation (e.g., through an upgrade, ownership change, or state mutation), the creator may believe the delegation is revoked. But `namespaceRemapping[pusher]` still points to the creator, so the pusher retains write authority. The creator might not call `removePushers` because they believe the delegation is already revoked (since `isPusher` now returns `false`).

This is the direct analog to the external bug: just as `max_slot` can be reduced after bids are submitted (making those bids silently unreachable), the `isPusher` consent can be "revoked" at the application level after delegation, but the delegation state in `namespaceRemapping` does not update to reflect this change — the pusher retains write authority.

The contrast with the EOA path is instructive: `allowPushers` requires a deadline precisely because *"the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* The contract-pusher path has the symmetric problem in reverse: the live check is done once, but the delegation persists after the pusher revokes consent. [3](#0-2) 

### Impact Explanation

The pusher can inject arbitrary prices (with valid timestamps and valid `Codebook256` spread indices) into the creator's namespace. These prices are consumed by `AnchoredPriceProvider` via `IPricedOracle(address(offchainOracle)).price(feedId, msg.sender)`: [4](#0-3) 

The `AnchoredPriceProvider` applies staleness and spread checks, but if the injected price has a recent timestamp and a valid (non-sentinel) spread index, it passes all guards. The pool then executes swaps at the manipulated mid price, causing:
- Traders to receive more or less than the oracle/bin curve permits (bad-price execution)
- LPs to suffer losses from swaps executed at a manipulated reference band

The `CompressedOracle.price()` ignores the `pool` parameter entirely (no abuse-protection layer), so any pool configured to use a feedId from the creator's namespace is affected: [5](#0-4) 

### Likelihood Explanation

Medium. Requires a contract pusher that changes its `isPusher` return value after delegation. This can happen through:
1. **Contract upgrade** (proxy pattern): new implementation changes the allowed-creator logic
2. **Ownership change**: new owner removes the creator from the allowed list
3. **State mutation**: the pusher contract's admin explicitly removes the creator from its internal registry

The creator's reasonable expectation — based on the "live check" design documented in the comment — is that the delegation is revoked when `isPusher` returns `false`. This expectation is violated by the code.

### Recommendation

One of the following mitigations:

1. **Re-check `isPusher` on every push** in `fallback()` for contract pushers (expensive but fully correct):
   ```solidity
   address creator = namespaceRemapping[msg.sender];
   if (creator != address(0)) {
       // Re-validate live consent
       (bool ok, bytes memory res) = msg.sender.staticcall(
           abi.encodeWithSignature("isPusher(address)", creator)
       );
       if (!ok || !abi.decode(res, (bool))) creator = address(0);
   }
   if (creator == address(0)) creator = msg.sender;
   ```

2. **Add a deadline to `allowContractPushers`** (symmetric with `allowPushers`), forcing periodic re-delegation and bounding the window during which a revoked pusher retains write authority.

3. **Document explicitly** that changing `isPusher` to return `false` does NOT revoke the delegation, and that `removePushers` must be called explicitly by the creator.

### Proof of Concept

```solidity
// 1. Deploy a mutable pusher contract
contract MutablePusher {
    bool public allowPush = true;
    address public oracle;

    constructor(address _oracle) { oracle = _oracle; }

    function isPusher(address) external view returns (bool) {
        return allowPush;
    }

    // Owner revokes consent at the application level
    function revokeConsent() external { allowPush = false; }

    // But can still push into creator's namespace
    function pushBadPrice(bytes memory payload) external {
        (bool ok,) = oracle.call(payload);
        require(ok, "push failed");
    }
}

// Step 1: creator delegates to MutablePusher
// vm.prank(creator);
// oracle.allowContractPushers([address(mutablePusher)]);
// → isPusher(creator) returns true → namespaceRemapping[mutablePusher] = creator

// Step 2: MutablePusher revokes consent at application level
// mutablePusher.revokeConsent();
// → isPusher(creator) now returns false
// → creator believes delegation is revoked (does NOT call removePushers)

// Step 3: MutablePusher pushes a manipulated price into creator's namespace
// uint56 tsMs = uint56(block.timestamp * 1000);
// uint48 badRaw = _packRaw(manipulatedPrice, validS0, validS1);
// bytes memory payload = _wordAt(slotId, positionIndex, badRaw, tsMs);
// mutablePusher.pushBadPrice(payload);
// → fallback() reads namespaceRemapping[mutablePusher] = creator (still set!)
// → manipulated price written to creator's namespace

// Step 4: AnchoredPriceProvider reads manipulated price
// → pool.swap() executes at manipulated bid/ask
// → traders and LPs suffer losses
```

The `namespaceRemapping` entry persists because `removePushers` was never called — the creator believed `isPusher` returning `false` was sufficient to revoke the delegation. [6](#0-5)

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L161-178)
```text
    /// @notice Unified read path shared with the providers oracle. The compressed oracle is open, so
    ///         `pool` is unused (no in-swap binding) and reads are permissionless.
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L214-234)
```text
    /// @notice Contract-pusher variant: consent is proven by a LIVE `isPusher(creator)`
    ///         staticcall instead of a signature, so there is nothing to replay and no
    ///         deadline is needed.
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
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L245-260)
```text
    function removePushers(address[] calldata pushers) external {
        uint256 l = pushers.length;
        for (uint256 i; i < l; i++) {
            address pusher = pushers[i];
            if (pusher == msg.sender) {
                revert NoSelfRemapping();
            }

            if (namespaceRemapping[pusher] == msg.sender) {
                namespaceRemapping[pusher] = address(0);
                emit PusherRevoked(pusher, msg.sender);
            } else {
                revert InvalidManager(msg.sender);
            }
        }
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
