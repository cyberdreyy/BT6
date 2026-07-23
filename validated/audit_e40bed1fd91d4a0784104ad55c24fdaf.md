### Title
`allowPushers` Consent Signature Is Never Consumed — Creator Can Replay It to Nullify a Pusher's `revokePusher` Call - (File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol)

---

### Summary

`allowPushers` validates a pusher's EIP-191 consent signature but never marks it as used. Because no nonce or used-signature bitmap exists, a creator who holds a still-valid (non-expired) consent signature can call `allowPushers` again with the **same bytes** immediately after the pusher calls `revokePusher()`, silently re-establishing the delegation. The pusher's self-revocation is therefore ineffective for the entire remaining deadline window, and every push the pusher makes continues to land in the creator's namespace rather than the pusher's own.

---

### Finding Description

In `allowPushers`, the pusher's consent is proven by recovering the signer from an EIP-191 hash over `(chainid, address(this), deadline, pusher, msg.sender)`: [1](#0-0) 

After the check passes, `namespaceRemapping[pusher] = msg.sender` is written and the function returns. **The signature bytes are never stored, hashed into a used-set, or otherwise invalidated.**

`revokePusher` clears the mapping: [2](#0-1) 

But because the original signature is still valid (the deadline has not expired), the creator can call `allowPushers` again in the very next transaction with the identical `(deadline, [pusher], [sig])` arguments. `_ensureDeadline` passes, ECDSA recovery succeeds, and `namespaceRemapping[pusher]` is set back to the creator. The pusher's revocation is undone.

The code comment acknowledges the deadline as the replay guard: [3](#0-2) 

But the deadline only bounds the outer window — it does **not** prevent the creator from replaying the same signature an unlimited number of times within that window. The analog to the external report is exact: just as `userTokenBalanceMap` was read but never reset (allowing unlimited withdrawals), the consent signature is validated but never consumed (allowing unlimited re-delegations after revocation).

The `fallback` push path resolves the namespace at call time: [4](#0-3) 

So every push the pusher makes after their revocation is undone still lands in the creator's namespace, not the pusher's own.

---

### Impact Explanation

A pusher who wants to stop contributing to a creator's namespace — for example because they discovered the creator's pool is configured to extract value from LPs, or because they want to redirect their feed to their own namespace — cannot do so until the deadline expires. The `revokePusher` invariant ("after revocation the wallet pushes into its OWN namespace again") is broken for the entire remaining deadline window. Every push the pusher makes continues to update the creator's feed, which is consumed by pools via the price provider chain: [5](#0-4) 

If the pusher is attempting to halt a feed they know is wrong (e.g., their signing infrastructure is misbehaving), the creator can keep the feed alive by replaying the consent, causing bad prices to continue reaching pool swaps.

---

### Likelihood Explanation

- The creator holds the signature bytes off-chain from the original `allowPushers` call.
- They only need to watch for the `PusherRevoked` event on-chain and respond in the next block.
- No special privilege is required — the creator is the ordinary `msg.sender` of `allowPushers`.
- Deadlines in practice are set days to months in the future, giving a large replay window.

---

### Recommendation

Track consumed signatures with a `mapping(bytes32 => bool) public usedConsents` keyed on the full hash, and revert if the hash has already been used:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(!usedConsents[hash], SignatureAlreadyUsed());
require(pusher == ECDSA.recover(hash, signatures[i]));
usedConsents[hash] = true;
```

Alternatively, record a per-pusher revocation timestamp in `revokePusher` and reject any consent signature whose deadline predates that timestamp.

---

### Proof of Concept

```solidity
function test_revoke_replay_restores_delegation() public {
    uint256 deadline = block.timestamp + 365 days;

    // 1. Pusher signs consent for creator
    bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

    address[] memory pushers = new address[](1);
    pushers[0] = pusher;
    bytes[] memory sigs = new bytes[](1);
    sigs[0] = sig;

    // 2. Creator establishes delegation
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs);
    assertEq(oracle.namespaceRemapping(pusher), creator, "delegation set");

    // 3. Pusher self-revokes
    vm.prank(pusher);
    oracle.revokePusher();
    assertEq(oracle.namespaceRemapping(pusher), address(0), "revoked");

    // 4. Creator replays the SAME signature — no revert, delegation restored
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs);
    assertEq(oracle.namespaceRemapping(pusher), creator, "delegation silently re-established");

    // 5. Pusher's next push still lands in creator's namespace, not pusher's own
    uint56 tsMs = uint56(block.timestamp * 1000);
    uint48 raw = _packRaw(900_000, 5, 0);
    vm.prank(pusher);
    (bool ok,) = address(oracle).call(_wordAt(2, 3, raw, tsMs));
    assertTrue(ok);

    // Creator namespace updated — pusher's revocation had zero effect
    assertEq(
        oracle.getOracleData(oracle.feedIdOf(creator, 2, 3)).price,
        U64x32.decode(uint32(raw >> 16)),
        "price landed in creator namespace despite revocation"
    );
    // Pusher's own namespace is still empty
    assertEq(oracle.getOracleData(oracle.feedIdOf(pusher, 2, 3)).price, 0);
}
``` [6](#0-5) [2](#0-1)

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

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L160-172)
```text
    function price(bytes32 feedId, address pool)
        external
        feedExists(feedId)
        notBlacklisted
        returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
    {
        require(pool != address(0) && IPool(pool).inSwap() == msg.sender, InvalidInSwap());
        require(!blacklisted[pool], Blacklisted(pool));
        require(registeredPool[feedId][pool], NotRegistered(feedId, pool));

        (mid, spread, spread1, refTime) = _readPrice(feedId);
        emit PriceRead(pool, feedId);
    }
```
