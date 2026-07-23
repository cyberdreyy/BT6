### Title
Pusher Consent Signature Has No Nonce, Allowing Creator to Re-Establish Delegation After Pusher Revokes — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary

`allowPushers` signs pusher consent over `(chainid, oracle, deadline, pusher, creator)` with no nonce. A creator who holds a valid pre-deadline signature can call `allowPushers` again after the pusher has called `revokePusher()`, silently re-establishing the delegation. The code's own NatSpec comment claims the deadline prevents exactly this, but the deadline only blocks use *after* it expires — it does not prevent replay within the validity window.

---

### Finding Description

`allowPushers` constructs the consent hash as:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
``` [1](#0-0) 

There is no nonce, no per-use invalidation flag, and no consumed-signature registry. The same `(deadline, pusher, creator)` tuple produces the same hash every time, so the signature is valid for an unlimited number of `allowPushers` calls until `block.timestamp >= deadline`.

`revokePusher` clears the mapping:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [2](#0-1) 

But nothing prevents the creator from immediately calling `allowPushers` again with the identical signature, writing `namespaceRemapping[pusher] = creator` back. The NatSpec comment on line 189–191 explicitly states the deadline is the guard against this scenario, yet the deadline only gates *expiry*, not *replay*: [3](#0-2) 

**Attack path:**

1. Pusher signs consent with `deadline = block.timestamp + 365 days`.
2. Creator calls `allowPushers` → `namespaceRemapping[pusher] = creator`.
3. Pusher discovers the creator's namespace is being used to feed bad/stale prices into a pool; pusher calls `revokePusher()` → `namespaceRemapping[pusher] = address(0)`.
4. Creator immediately calls `allowPushers` with the **same** signature → `namespaceRemapping[pusher] = creator` is restored.
5. Steps 3–4 repeat for up to one year; the pusher's revocation is permanently ineffective.

The fallback push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

So every push the pusher makes after the re-established delegation still lands in the creator's namespace, not the pusher's own.

---

### Impact Explanation

The compressed oracle is the price source for `PriceProvider` / `AnchoredPriceProvider` / `PriceProviderL2`, which feed bid/ask quotes directly into `MetricOmmPool.swap()`. If a pusher revokes because their price data is compromised (e.g., a keeper script is feeding stale or manipulated prices), the creator can nullify that revocation and keep the bad data flowing into their namespace. Pools reading that feed will execute swaps at incorrect bid/ask prices, causing direct loss of funds to traders or LPs — a bad-price execution impact.

---

### Likelihood Explanation

The creator must have obtained the pusher's signature beforehand (normal setup flow). Any creator who acts adversarially after a pusher revokes can replay the signature with a single transaction. The pusher has no on-chain way to detect the re-establishment without monitoring events, and no way to permanently invalidate the old signature short of waiting for the deadline. Likelihood is **Medium**: requires a malicious or negligent creator, but the mechanism is fully permissionless and requires no special privilege.

---

### Recommendation

Add a per-pusher nonce to the consent hash and increment it on every successful `allowPushers` call (or on every `revokePusher` call). For example:

```solidity
mapping(address => uint256) public pusherNonce;

// in allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusherNonce[pusher],   // <-- added
        pusher, msg.sender
    ))
);
pusherNonce[pusher]++;         // invalidate on use
```

Alternatively, record each consumed `(pusher, deadline, creator)` tuple in a `usedConsents` mapping and revert on re-use.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent (off-chain, deadline = far future)
bytes memory sig = sign(PUSHER_KEY,
    keccak256(abi.encode(chainid, oracle, deadline, pusher, creator)));

// 2. Creator establishes delegation
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator); // delegated

// 3. Pusher revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0)); // cleared

// 4. Creator replays the SAME signature — no revert
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator); // re-established!

// 5. Pusher's subsequent pushes still land in creator's namespace
vm.prank(pusher);
(bool ok,) = address(oracle).call(buildPushWord(slotId, raw, tsMs));
assertTrue(ok);
// creator's feed is updated; pusher's own namespace stays empty
assertGt(oracle.getOracleData(oracle.feedIdOf(creator, slotId, pos)).price, 0);
assertEq(oracle.getOracleData(oracle.feedIdOf(pusher,  slotId, pos)).price, 0);
```

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
