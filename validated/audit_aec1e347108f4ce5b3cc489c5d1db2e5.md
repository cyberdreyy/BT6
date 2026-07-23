### Title
Delegation consent signature lacks a nonce, allowing a creator to silently re-establish a pusher's revoked delegation within the deadline window — (`File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` verifies a pusher's EIP-191 consent signature over `(chainid, address(this), deadline, pusher, creator)`. No nonce or revocation-state indicator is included in the signed message. Because the same signature is valid for every call to `allowPushers` until the deadline expires, a creator who holds a pusher's consent signature can re-establish the delegation immediately after the pusher calls `revokePusher()`, making the revocation ineffective for the entire remaining deadline window.

---

### Finding Description

`allowPushers` computes the signed digest as:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
``` [1](#0-0) 

There is no nonce, no "already-used" bitmap, and no reference to the current `namespaceRemapping[pusher]` state. The only time-bound is the `deadline` field, which is checked by `_ensureDeadline`:

```solidity
function _ensureDeadline(uint256 deadline) internal view virtual {
    require(block.timestamp <= deadline, DeadlineExceeded());
}
``` [2](#0-1) 

The code comment on `allowPushers` acknowledges the risk but claims the deadline is the solution:

> "The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it." [3](#0-2) 

However, the deadline only prevents re-establishment **after** the deadline expires. Within the deadline window the creator can call `allowPushers` again with the identical signature bytes, overwriting `namespaceRemapping[pusher]` back to `creator` immediately after the pusher's `revokePusher()` clears it:

```solidity
// revokePusher clears the mapping:
namespaceRemapping[msg.sender] = address(0);   // pusher's call

// creator immediately replays the original signature:
namespaceRemapping[pusher] = msg.sender;        // allowPushers re-sets it
``` [4](#0-3) [5](#0-4) 

The `fallback` push path then resolves the namespace from the (now-restored) mapping:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [6](#0-5) 

So every subsequent push by the pusher — even pushes the pusher believes are going to their own namespace — silently lands in the creator's namespace.

---

### Impact Explanation

After revocation is undone, the pusher's calldata is written into the creator's storage namespace. If the pusher has changed the semantic meaning of their slot words (e.g., they now push BTC/USD data into slot 0, position 0, whereas the creator's pool expects ETH/USD at that location), the creator's feed is overwritten with a wrong price. Any pool that calls `price(feedId, pool)` on the creator's feed during a swap will receive the corrupted mid-price and spread, executing the swap at a bad oracle quote. This satisfies the "bad-price execution" impact gate: a stale or wrong bid/ask quote reaches a live pool swap.

---

### Likelihood Explanation

- The creator already holds the pusher's valid signature (they used it to establish the original delegation).
- The creator only needs to call `allowPushers` again in the same transaction or block as the pusher's `revokePusher()`.
- No privileged role is required; `allowPushers` is a public function callable by any address.
- The window of exposure equals the remaining time until the deadline, which can be hours or days depending on the deadline chosen by the creator.
- The pusher has no on-chain mechanism to invalidate the signature before the deadline expires.

---

### Recommendation

Add a per-pusher nonce to the signed digest and increment it on every successful delegation or revocation:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender,
        pusherNonce[pusher]   // <-- add nonce
    ))
);
pusherNonce[pusher]++;        // invalidate after use
```

Alternatively, burn the signature on first use by storing a `usedSignatures` bitmap keyed on the digest. Either approach ensures that once a pusher revokes, the creator cannot replay the original consent.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent for creator, deadline = now + 1 day
bytes memory sig = _signConsent(PUSHER_KEY, block.timestamp + 1 days, pusher, creator);

// 2. Creator establishes delegation
vm.prank(creator);
oracle.allowPushers(block.timestamp + 1 days, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator); // delegated

// 3. Pusher revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0)); // revoked

// 4. Creator immediately replays the SAME signature — no revert
vm.prank(creator);
oracle.allowPushers(block.timestamp + 1 days, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator); // delegation restored!

// 5. Pusher pushes BTC/USD data thinking it goes to their own namespace
uint56 tsMs = uint56(block.timestamp * 1000);
uint48 btcRaw = _packRaw(BTC_PRICE, 5, 5);
vm.prank(pusher);
(bool ok,) = address(oracle).call(_wordAt(0, 0, btcRaw, tsMs));
assertTrue(ok);

// 6. Data landed in CREATOR's namespace (ETH/USD pool feed), not pusher's own
IOffchainOracle.OracleData memory d = oracle.getOracleData(oracle.feedIdOf(creator, 0, 0));
assertEq(d.price, U64x32.decode(uint32(btcRaw >> 16))); // BTC price in ETH/USD slot → bad price
assertEq(oracle.getOracleData(oracle.feedIdOf(pusher, 0, 0)).price, 0); // pusher's own feed empty
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L204-207)
```text
            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L209-210)
```text
            namespaceRemapping[pusher] = msg.sender;
            emit PusherAuthorized(pusher, msg.sender);
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L238-242)
```text
    function revokePusher() external {
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
        namespaceRemapping[msg.sender] = address(0);
        emit PusherRevoked(msg.sender, creator);
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-316)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;
```

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L124-126)
```text
    function _ensureDeadline(uint256 deadline) internal view virtual {
        require(block.timestamp <= deadline, DeadlineExceeded());
    }
```
