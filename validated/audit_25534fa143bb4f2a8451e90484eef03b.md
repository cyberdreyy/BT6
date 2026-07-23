### Title
Creator Can Replay Pusher Consent Signature to Nullify `revokePusher()` Within Deadline Window, Causing Stale Oracle Prices in Pools — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers()` in `CompressedOracleV1` contains no used-signature registry or nonce. A creator who holds a valid pusher consent signature can replay it an unlimited number of times before the deadline, re-establishing delegation immediately after the pusher calls `revokePusher()`. This makes `revokePusher()` ineffective for the entire deadline window and allows the creator to keep the pusher's fallback writes redirected away from the pusher's own namespace, causing stale prices in any pool that reads from that namespace.

---

### Finding Description

`allowPushers` verifies a pusher's EIP-191 consent signature and writes `namespaceRemapping[pusher] = msg.sender`: [1](#0-0) 

The signed message binds `(chainid, address(this), deadline, pusher, creator)`. There is no nonce and no used-signature bitmap. The only replay guard is the deadline check: [2](#0-1) 

`revokePusher()` clears the mapping: [3](#0-2) 

Because the same signature is valid for any call before `block.timestamp > deadline`, the creator can immediately re-call `allowPushers` with the identical `(deadline, [pusher], [sig])` tuple after every `revokePusher()`, restoring `namespaceRemapping[pusher] = creator` in the same block.

The code's own NatDoc acknowledges the concern but treats the deadline as a complete fix: [4](#0-3) 

The deadline limits the *outer* window but does nothing to prevent within-window replay after revocation.

The `fallback()` push path resolves the namespace at call time: [5](#0-4) 

So every push the pusher makes while the delegation is re-active lands in the creator's namespace, not the pusher's own. The pusher's own namespace (`feedId = pusher_address | chainid | slotIndex | positionIndex`) receives no updates and its timestamp goes stale.

---

### Impact Explanation

Any `PriceProvider` or `AnchoredPriceProvider` configured to read from the pusher's own namespace feedId will receive a stale timestamp and a frozen price. When a pool calls `swap`, the provider reads this stale quote and the pool executes at a price that no longer reflects the market. This is a direct bad-price execution path:

```
pool.swap
  → provider.getBidAndAskPrice
    → oracle.price(feedId_of_pusher_namespace, pool)
      → getOracleData → _loadSlotLayout → stale timestamp / frozen price
``` [6](#0-5) 

The creator can sustain this condition for the full deadline window (which the pusher chose when signing — potentially days or weeks). Traders swapping against the pool during this window receive incorrect execution prices, constituting direct loss of user principal.

---

### Likelihood Explanation

The attack requires the creator to hold a valid pusher consent signature. This is a normal part of the delegation flow — the pusher signs and hands the signature to the creator as part of setting up a legitimate data-feed arrangement. If the relationship sours (e.g., the creator starts pushing manipulated prices into their own namespace and the pusher wants to stop contributing), the pusher calls `revokePusher()`. The creator can immediately replay the original signature. No privileged role, no admin key, and no special setup beyond the already-obtained signature is needed. The trigger is fully unprivileged and reachable in a single transaction.

---

### Recommendation

Track consumed signatures with a `mapping(bytes32 => bool) public usedDelegationSignatures` and mark each hash as used on first acceptance:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(!usedDelegationSignatures[hash], "signature already consumed");
require(pusher == ECDSA.recover(hash, signatures[i]));
usedDelegationSignatures[hash] = true;
namespaceRemapping[pusher] = msg.sender;
```

Alternatively, include a per-pusher nonce in the signed message so each consent is single-use by construction.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent with a 1-day deadline.
uint256 deadline = block.timestamp + 1 days;
bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

address[] memory pushers = new address[](1);
pushers[0] = pusher;
bytes[] memory sigs = new bytes[](1);
sigs[0] = sig;

// 2. Creator establishes delegation.
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs);
assertEq(oracle.namespaceRemapping(pusher), creator);

// 3. Pusher revokes.
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0));

// 4. Creator replays the SAME signature — no revert, delegation restored.
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs);
assertEq(oracle.namespaceRemapping(pusher), creator); // revocation nullified

// 5. Pusher's fallback pushes now land in creator's namespace again.
//    Pusher's own namespace (used by the pool) receives no updates → stale price.
uint56 tsMs = uint56(block.timestamp * 1000);
uint48 raw = _packRaw(1_000_000, 4, 2);
vm.prank(pusher);
(bool ok,) = address(oracle).call(_wordAt(0, 0, raw, tsMs));
assertTrue(ok);

// Push landed in creator's namespace, NOT pusher's.
assertEq(oracle.getOracleData(oracle.feedIdOf(creator, 0, 0)).price,
         U64x32.decode(uint32(raw >> 16)));
assertEq(oracle.getOracleData(oracle.feedIdOf(pusher, 0, 0)).price, 0); // stale
```

The pool configured on `feedIdOf(pusher, 0, 0)` now reads a zero/stale price for the entire remaining deadline window, regardless of how many times the pusher calls `revokePusher()`.

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L101-117)
```text
    function getOracleData(bytes32 feedId) public view override returns (OracleData memory data) {
        (address creator, uint8 slotIndex, uint8 positionIndex) = _unpackFeedId(feedId);

        SlotLayout memory _layout = _loadSlotLayout(_oracleSlot(creator, slotIndex));
        CompressedOracleData memory compressed = _selectCompressedData(_layout, positionIndex);

        if (compressed.s1 == 0xff && compressed.s0 == 0xff) {
            data.spread1 = BPS_BASE;
            data.spread0 = BPS_BASE;
            return data;
        }

        data.price = U64x32.decode(compressed.p);
        data.spread0 = _decodeCodebookIndex(compressed.s0);
        data.spread1 = _decodeCodebookIndex(compressed.s1);
        data.timestampMs = _layout.timestampMs;
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

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L124-126)
```text
    function _ensureDeadline(uint256 deadline) internal view virtual {
        require(block.timestamp <= deadline, DeadlineExceeded());
    }
```
