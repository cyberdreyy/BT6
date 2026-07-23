### Title
`revokePusher()` is Ineffective While the Original Consent Signature's Deadline Remains Valid — (`File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary

`allowPushers` does not track whether a consent signature has already been consumed. A creator who holds a pusher's signed consent can replay it an unlimited number of times before the deadline expires, silently re-establishing delegation immediately after the pusher calls `revokePusher()`. The pusher's revocation is a no-op for the entire lifetime of the original signature.

### Finding Description

`allowPushers` signs consent as:

```
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

There is no nonce, no used-signature set, and no check that `namespaceRemapping[pusher]` is currently `address(0)` before writing. The function unconditionally overwrites the mapping:

```solidity
namespaceRemapping[pusher] = msg.sender;
```

`revokePusher()` clears the mapping:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [2](#0-1) 

Because the same `(deadline, pusher, creator)` tuple produces the same valid signature every time, the creator can call `allowPushers` again with the identical signature immediately after the pusher's `revokePusher()` transaction is confirmed — or even in the same block by front-running. The cycle repeats indefinitely until the deadline timestamp passes.

The code's own comment acknowledges the deadline is required to prevent post-revocation replay, but it only prevents replay *after* the deadline, not *before* it: [3](#0-2) 

### Impact Explanation

The attack path that reaches pool pricing:

1. Pusher P is delegated to creator C (`namespaceRemapping[P] = C`). P's `fallback()` pushes land in `feedIdOf(C, slot, pos)`.
2. P decides to stop serving C and start their own oracle feed. P calls `revokePusher()` → `namespaceRemapping[P] = address(0)`.
3. C immediately calls `allowPushers(sameDeadline, [P], [sameSig])` → `namespaceRemapping[P] = C` again.
4. P, unaware the delegation was silently re-established, pushes data believing it goes to `feedIdOf(P, slot, pos)` (their own namespace).
5. The `fallback()` namespace resolution reads `namespaceRemapping[P] = C` and writes to `feedIdOf(C, slot, pos)` instead. [4](#0-3) 

6. P's own pool, configured with `feedIdOf(P, slot, pos)`, reads a timestamp of 0 (never updated). `AnchoredPriceProvider._readLeg` calls `_isStale(0, block.timestamp, MAX_REF_STALENESS)` → `true` (refTime == 0 is always stale). [5](#0-4) 

7. `getBidAndAskPrice()` returns `(0, type(uint128).max)` → `FeedStalled` revert. Every swap on P's pool halts. [6](#0-5) 

C's pool continues to receive P's price data. P's pool is permanently DoS'd on swaps for the duration of the deadline window.

### Likelihood Explanation

- The creator holds the pusher's consent signature off-chain (they needed it to call `allowPushers` the first time).
- Re-establishing delegation costs one transaction and requires no new signature from the pusher.
- The creator can monitor the mempool and front-run `revokePusher()` in the same block, or simply call `allowPushers` in the next block.
- The window lasts for the full deadline duration (up to any value the pusher agreed to when signing).

### Recommendation

Track consumed signatures with a `mapping(bytes32 => bool) usedSignatures` and mark each signature hash as used on first acceptance:

```solidity
bytes32 sigHash = keccak256(signatures[i]);
require(!usedSignatures[sigHash], "SignatureAlreadyUsed");
usedSignatures[sigHash] = true;
```

Alternatively, require that `namespaceRemapping[pusher] == address(0)` before establishing a new delegation, so a pusher who has revoked cannot be re-delegated without a fresh signature.

### Proof of Concept

```solidity
// 1. Pusher signs consent for creator (deadline = now + 1 day)
uint256 deadline = block.timestamp + 1 days;
bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

// 2. Creator establishes delegation
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator);

// 3. Pusher revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0));

// 4. Creator immediately replays the SAME signature — no new consent needed
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig)); // same sig, same deadline
assertEq(oracle.namespaceRemapping(pusher), creator);   // delegation restored

// 5. Pusher pushes, believing data goes to feedIdOf(pusher, 0, 0)
uint56 tsMs = uint56(block.timestamp * 1000);
vm.prank(pusher);
(bool ok,) = address(oracle).call(_wordAt(0, 0, _packRaw(1_000_000, 3, 3), tsMs));
assertTrue(ok);

// 6. Data landed in CREATOR namespace, not pusher's own
assertEq(oracle.getOracleData(oracle.feedIdOf(creator, 0, 0)).price, U64x32.decode(1_000_000));
assertEq(oracle.getOracleData(oracle.feedIdOf(pusher,  0, 0)).price, 0); // pusher's pool sees zero → FeedStalled
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L186-192)
```text
    /// @notice Delegates pusher wallets into the caller's namespace. The pusher's EIP-191
    ///         signature is REQUIRED — without it anyone could remap a foreign pusher
    ///         wallet into their own namespace and silently swallow its pushes. The
    ///         deadline is likewise required: the signed consent carries no timestamp of
    ///         its own, so an undated signature could re-establish a delegation AFTER the
    ///         pusher revoked it.
    function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L204-210)
```text
            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));

            namespaceRemapping[pusher] = msg.sender;
            emit PusherAuthorized(pusher, msg.sender);
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

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L226-229)
```text
    ) internal pure returns (bool) {
        if (refTime == 0) return true;
        if (refTime > nowTs) return true;
        return (nowTs - refTime) > maxDelta;
```
