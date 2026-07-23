### Title
Pusher revocation is not permanent — creator can replay the same signed consent to re-establish delegation after `revokePusher()` — (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`revokePusher()` clears `namespaceRemapping[pusher]` but does not invalidate the pusher's signed consent. Within the deadline window the creator can call `allowPushers()` again with the **identical signature** to silently re-establish delegation, nullifying the pusher's self-revocation and allowing a compromised pusher key to resume injecting prices into the creator's oracle namespace.

---

### Finding Description

The `allowPushers` NatSpec comment explicitly acknowledges the replay risk:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."*

The deadline is intended to bound the replay window, but it does not close it. `allowPushers` performs only two checks before writing `namespaceRemapping[pusher] = msg.sender`:

1. `_ensureDeadline(deadline)` — passes as long as `block.timestamp <= deadline`
2. `ECDSA.recover(hash, signatures[i]) == pusher` — passes for any valid signature, regardless of how many times it has already been used [1](#0-0) 

There is no nonce, no used-signature bitmap, and no per-pusher revocation epoch. Consequently, after `revokePusher()` zeroes the mapping: [2](#0-1) 

the creator can immediately call `allowPushers` again with the same `(deadline, pusher, sig)` tuple and the mapping is restored. The pusher's revocation is silently overwritten.

The `fallback` push path resolves the namespace from `namespaceRemapping[msg.sender]`, so any subsequent push from the pusher's address lands in the **creator's** namespace, not the pusher's own: [3](#0-2) 

---

### Impact Explanation

If a pusher's private key is compromised:

1. The pusher calls `revokePusher()` to stop the attacker from writing into the creator's feed namespace.
2. The creator — unaware of the compromise, or executing a routine re-delegation — calls `allowPushers` with the same still-valid signature.
3. The attacker, holding the compromised key, resumes pushing arbitrary `(price, spread0, spread1, timestampMs)` tuples into the creator's namespace.
4. The `AnchoredPriceProvider` (or any pool that reads this feed) consumes the manipulated bid/ask, causing bad-price execution: traders receive more output than the oracle permits or the pool receives less input than owed.

This satisfies the **bad-price execution** impact gate: a manipulated bid/ask quote reaches a pool swap.

---

### Likelihood Explanation

- The creator re-calling `allowPushers` after a pusher revokes is a realistic operational pattern (e.g., automated key-rotation scripts, monitoring bots that re-establish delegation on any mapping-clear event).
- The deadline window can be set to days or weeks, giving the attacker a large window to exploit the re-established delegation.
- No on-chain signal distinguishes a "revoked and re-established" mapping from a freshly established one, so off-chain monitoring cannot easily detect the replay.

---

### Recommendation

Track consumed consent signatures with a per-pusher revocation nonce or a used-signature set. The simplest fix is to add a per-pusher revocation counter to the signed payload:

```solidity
// storage
mapping(address => uint256) public pusherRevocationNonce;

// in revokePusher():
pusherRevocationNonce[msg.sender]++;

// in allowPushers(), include the current nonce in the signed hash:
keccak256(abi.encode(
    block.chainid,
    address(this),
    deadline,
    pusher,
    msg.sender,
    pusherRevocationNonce[pusher]   // <-- add this
))
```

After `revokePusher()` increments the nonce, any previously issued signature (which committed to the old nonce) becomes permanently invalid, making revocation irreversible.

---

### Proof of Concept

```solidity
function testRevokeAndReDelegate_SameSignatureReplay() public {
    uint256 deadline = block.timestamp + 1 days;

    // Pusher signs consent once
    bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

    address[] memory pushers = new address[](1);
    pushers[0] = pusher;
    bytes[] memory sigs = new bytes[](1);
    sigs[0] = sig;

    // Step 1: Creator establishes delegation
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs);
    assertEq(oracle.namespaceRemapping(pusher), creator, "delegation established");

    // Step 2: Pusher self-revokes (key compromised)
    vm.prank(pusher);
    oracle.revokePusher();
    assertEq(oracle.namespaceRemapping(pusher), address(0), "revoked");

    // Step 3: Creator replays the SAME signature — succeeds, no revert
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs);
    assertEq(oracle.namespaceRemapping(pusher), creator, "delegation silently re-established");

    // Step 4: Attacker (compromised key) pushes a manipulated price into creator's namespace
    uint56 tsMs = uint56(block.timestamp * 1000);
    uint48 badRaw = _packRaw(9_999_999, 8, 8);
    vm.prank(pusher);                                    // attacker using stolen key
    (bool ok,) = address(oracle).call(_wordAt(0, 0, badRaw, tsMs));
    assertTrue(ok, "bad price push succeeded");

    IOffchainOracle.OracleData memory data =
        oracle.getOracleData(oracle.feedIdOf(creator, 0, 0));
    assertEq(
        data.price,
        U64x32.decode(uint32(badRaw >> 16)),
        "manipulated price now in creator namespace, feeds into pool"
    );
}
```

### Citations

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
