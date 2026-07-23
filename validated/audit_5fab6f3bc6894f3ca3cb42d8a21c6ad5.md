### Title
Creator can replay `allowPushers` EIP-191 signature to re-establish pusher delegation after pusher self-revokes, silently redirecting price pushes into oracle feeds — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.allowPushers` uses a deadline-bounded EIP-191 signature with no per-delegation nonce or invalidation flag. After a pusher calls `revokePusher()`, the creator can replay the original `allowPushers` calldata (same `deadline`, same `signatures`) within the deadline window to silently re-establish the delegation, overriding the pusher's explicit revocation and redirecting the pusher's subsequent price pushes into the creator's oracle namespace.

---

### Finding Description

`allowPushers` verifies a pusher's EIP-191 consent signature covering `(block.chainid, address(this), deadline, pusher, msg.sender)`: [1](#0-0) 

There is no nonce, no per-pusher revocation counter, and no invalidation flag. The only replay guard is the deadline, which the creator supplies as a parameter and can set arbitrarily far in the future (no maximum is enforced in `_ensureDeadline`): [2](#0-1) 

When a pusher calls `revokePusher()`, it clears `namespaceRemapping[msg.sender]` to `address(0)`: [3](#0-2) 

Because the original signature is still cryptographically valid (deadline not expired, same `msg.sender`/creator), the creator can immediately call `allowPushers` again with the identical `deadline`, `pushers`, and `signatures` arrays. The check passes and `namespaceRemapping[pusher]` is set back to the creator: [4](#0-3) 

The code's own NatSpec acknowledges the concern but only addresses the *undated* case:

> "The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."

The deadline limits the window but does not prevent replay within it. The pusher's revocation is not final.

After re-establishment, the `fallback` push path resolves the creator namespace from `namespaceRemapping[msg.sender]`: [5](#0-4) 

The pusher, believing they are now pushing into their own namespace, continues to push data. All pushes are silently written into the creator's namespace instead.

---

### Impact Explanation

The pusher may have revoked because they are now pushing data for a different asset or a different protocol at the same `(slotIndex, positionIndex)` coordinates. After the creator replays `allowPushers`, those pushes land in the creator's namespace at `feedIdOf(creator, slotIndex, positionIndex)`. Any pool consuming that feed receives prices that were never intended for it — a direct bad-price execution path. The `feedId` packs `(creator, chainid, slotIndex, positionIndex)`: [6](#0-5) 

A pool reading `feedIdOf(creator, 0, 0)` for ETH/USD would receive BTC/USD prices if the pusher switched assets after revoking. The monotonicity check only prevents *older* timestamps from overwriting newer ones; it does not detect asset mismatch: [7](#0-6) 

---

### Likelihood Explanation

- The creator controls the `deadline` parameter and can set it to `type(uint256).max`, making the replay window permanent.
- The replay requires only that the creator re-submit the original transaction calldata — no new signature is needed.
- The pusher has no on-chain mechanism to detect or prevent the re-establishment.
- The scenario is realistic whenever a pusher leaves a creator's service and begins pushing different data for their own namespace.

---

### Recommendation

Add a per-pusher revocation nonce to the signed message. Increment it in `revokePusher()` and `removePushers()`. Include the nonce in the `allowPushers` signature hash so that any signature produced before a revocation becomes invalid:

```solidity
mapping(address => uint256) public pusherNonce;

// In revokePusher / removePushers:
pusherNonce[pusher]++;

// In allowPushers signature hash:
keccak256(abi.encode(
    block.chainid,
    address(this),
    deadline,
    pusher,
    msg.sender,
    pusherNonce[pusher]   // <-- added
))
```

This is the standard nonce pattern used by EIP-2612 and EIP-712 permit flows to make revocation permanent.

---

### Proof of Concept

```solidity
// 1. Creator allows pusher with a 1-year deadline
uint256 deadline = block.timestamp + 365 days;
bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);
address[] memory pushers = new address[](1); pushers[0] = pusher;
bytes[] memory sigs = new bytes[](1);        sigs[0] = sig;

vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs);
// namespaceRemapping[pusher] == creator ✓

// 2. Pusher revokes
vm.prank(pusher);
oracle.revokePusher();
// namespaceRemapping[pusher] == address(0) ✓

// 3. Creator replays the IDENTICAL calldata — same deadline, same sig
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs);   // no revert
// namespaceRemapping[pusher] == creator again ✓  ← revocation overridden

// 4. Pusher pushes BTC/USD into what they believe is their own namespace
uint56 tsMs = uint56(block.timestamp * 1000);
uint48 btcRaw = _packRaw(BTC_PRICE, 5, 0);
vm.prank(pusher);
(bool ok,) = address(oracle).call(_wordAt(0, 0, btcRaw, tsMs));
assertTrue(ok);

// 5. Creator's ETH/USD pool reads feedIdOf(creator, 0, 0) → gets BTC price
IOffchainOracle.OracleData memory data = oracle.getOracleData(
    oracle.feedIdOf(creator, 0, 0)
);
assertEq(data.price, U64x32.decode(BTC_PRICE)); // bad price in ETH/USD pool
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L49-53)
```text
    function feedIdOf(address creator, uint8 slotIndex, uint8 positionIndex) public view returns (bytes32) {
        return bytes32(
            uint256(uint160(creator)) << 96 | block.chainid << 16 | uint256(slotIndex) << 8 | positionIndex
        );
    }
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L340-343)
```text
            bool newer = timestampMs.isAfter(oldTimestampMs);
            if (!newer) continue;

            _writeStorage(key, bytes32(bytes32(word & ~uint256(0xff))));
```

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L124-126)
```text
    function _ensureDeadline(uint256 deadline) internal view virtual {
        require(block.timestamp <= deadline, DeadlineExceeded());
    }
```
