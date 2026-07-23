### Title
`revokePusher` is ineffective: creator can replay a valid `allowPushers` signature to permanently re-establish delegation after pusher revokes — (`File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracle.allowPushers` accepts a deadline-bound EIP-191 signature as the pusher's consent. Because no nonce or consumed-flag is recorded, the creator can call `allowPushers` again with the **exact same signature** immediately after the pusher calls `revokePusher`, restoring the delegation. The pusher cannot permanently exit the creator's namespace until the deadline they originally signed has expired.

---

### Finding Description

`allowPushers` hashes and verifies:

```
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

After verification it unconditionally overwrites `namespaceRemapping[pusher] = msg.sender` with no check on the current mapping value and no nonce consumed. [2](#0-1) 

`revokePusher` clears the mapping to `address(0)`: [3](#0-2) 

`_ensureDeadline` only checks `block.timestamp <= deadline`: [4](#0-3) 

The code's own NatSpec acknowledges the exact attack vector but incorrectly claims the deadline is the fix:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [5](#0-4) 

The deadline limits the **window** but does not limit the **number of replays** within that window. A creator who holds a signature with `deadline = now + 365 days` can call `allowPushers` with the identical calldata an unlimited number of times, restoring the delegation every time the pusher revokes it.

The `fallback` push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [6](#0-5) 

So every push the pusher makes after the creator replays the delegation lands in the **creator's namespace**, not the pusher's own, regardless of the pusher's intent.

---

### Impact Explanation

The creator's namespace is the data source for a `feedId` consumed by a pool via `AnchoredPriceProvider` / `ProtectedPriceProvider`. If the pusher is an automated price-feed bot that continues pushing after revocation (a normal operational assumption), the creator can keep redirecting those pushes into their namespace indefinitely. If the pusher has switched to pushing data for a different asset, different scale, or different spread encoding after revocation, the creator's feed — and any pool reading it — receives price data that was never intended for that namespace. This satisfies the **bad-price execution** impact gate: an unexpected bid/ask quote reaches a live pool swap.

Additionally, the pusher's `revokePusher` call is rendered meaningless, breaking the security invariant that a pusher can unilaterally exit a delegation.

---

### Likelihood Explanation

The creator needs only to retain the original calldata from the first `allowPushers` call (trivially available from transaction history or mempool) and re-submit it. No special privilege, no new signature from the pusher, no on-chain state to manipulate. The only natural expiry is the deadline the pusher originally signed — which in practice is set far in the future to avoid operational friction.

---

### Recommendation

Record a per-`(pusher, creator)` nonce in the signature and increment it on each successful `allowPushers`. Alternatively, record a `revokedAt` timestamp per pusher and reject any `allowPushers` call whose signature predates the most recent revocation. The simplest fix:

```solidity
mapping(address pusher => uint256) public pusherNonce;

// in allowPushers:
uint256 nonce = pusherNonce[pusher]++;
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender, nonce))
);
```

This makes every delegation consent single-use: once the pusher revokes, the nonce advances and the old signature is permanently invalid.

---

### Proof of Concept

```solidity
function testRevokePusherBypassedByCreatorReplay() public {
    uint256 deadline = block.timestamp + 365 days;

    // Pusher signs consent once
    bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);
    address[] memory pushers = new address[](1);
    pushers[0] = pusher;
    bytes[] memory sigs = new bytes[](1);
    sigs[0] = sig;

    // Step 1: creator establishes delegation
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs);
    assertEq(oracle.namespaceRemapping(pusher), creator);

    // Step 2: pusher revokes
    vm.prank(pusher);
    oracle.revokePusher();
    assertEq(oracle.namespaceRemapping(pusher), address(0));

    // Step 3: creator replays the IDENTICAL signature — no new consent from pusher
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs);

    // Delegation is restored despite revocation
    assertEq(oracle.namespaceRemapping(pusher), creator);

    // Step 4: pusher's next push (intended for own namespace) lands in creator's namespace
    uint56 tsMs = uint56(block.timestamp * 1000);
    uint48 raw = _packRaw(999_999, 5, 0);
    vm.prank(pusher);
    (bool ok,) = address(oracle).call(_wordAt(2, 3, raw, tsMs));
    assertTrue(ok);

    // Creator's feed (consumed by a pool) now holds the pusher's unintended data
    IOffchainOracle.OracleData memory data = oracle.getOracleData(
        oracle.feedIdOf(creator, 2, 3)
    );
    assertEq(data.price, U64x32.decode(uint32(raw >> 16)));

    // Pusher's own namespace is empty — the push was silently hijacked
    assertEq(oracle.getOracleData(oracle.feedIdOf(pusher, 2, 3)).price, 0);
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
