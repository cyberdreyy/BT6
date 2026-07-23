### Title
`allowPushers` delegation signature is replayable within the deadline window, making `revokePusher` permanently ineffective until deadline expiry — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` uses a deadline to prevent a creator from re-establishing a pusher's delegation after the deadline expires. However, there is no nonce or used-signature tracking, so the creator can replay the exact same EIP-191 signature any number of times **within** the deadline window. A pusher who calls `revokePusher()` to escape a malicious creator is immediately re-delegated in the next block, making revocation completely ineffective until the deadline timestamp passes.

---

### Finding Description

The `allowPushers` function's own NatSpec comment acknowledges the exact threat:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."*

The deadline is intended to be the sole protection against this re-establishment. But the deadline only blocks replay **after** it expires — it does nothing to prevent the creator from replaying the same signature **before** expiry. [1](#0-0) 

The signed message is:
```
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
```

This hash is identical on every call with the same `(deadline, pusher, creator)` triple. There is no nonce, no per-signature invalidation map, and no on-chain record that the signature was already consumed. After `revokePusher()` clears `namespaceRemapping[pusher] = address(0)`, the creator calls `allowPushers` again with the identical calldata and the mapping is restored to `creator`. [2](#0-1) 

The `revokePusher` state update (`namespaceRemapping[pusher] = address(0)`) is incomplete: it clears one state variable but leaves the complementary state — the signature's validity — entirely intact. This is the direct analog of the external "complicated state updates" bug class: a revocation that touches only one of the two coupled state components.

---

### Impact Explanation

Once a pusher is re-delegated against their will, every call they make to the oracle `fallback()` resolves their namespace to the creator's address: [3](#0-2) 

The pusher has two choices, both harmful:

1. **Continue pushing** — all price updates land in the creator's namespace, not the pusher's own. The pusher's own feeds (used by pools) receive zero updates and become stale.
2. **Stop pushing** — the pusher's own feeds also become stale.

Either path causes `AnchoredPriceProvider._readLeg()` to return `ok = false` on the staleness check: [4](#0-3) 

`getBidAndAskPrice()` then reverts with `FeedStalled`: [5](#0-4) 

Every pool swap that calls `getBidAndAskPrice()` reverts. Liquidity providers cannot swap, and depending on pool design, withdraw flows that require a price quote are also blocked. This is broken core pool functionality causing unusable swap flows — a contest-relevant impact.

---

### Likelihood Explanation

- The pusher must have signed a consent with a future deadline (normal operational practice for any pusher who intends to be delegated for a period).
- The creator must be malicious or compromised.
- No special on-chain conditions are required; the creator replays the original calldata in a single transaction immediately after the pusher's `revokePusher()` lands.
- The attack repeats indefinitely until the deadline timestamp passes, so the pusher has no on-chain remedy during that window.

Likelihood: **Medium** — requires a malicious creator but zero additional privileges or setup beyond possessing the original signed consent.

---

### Recommendation

Track consumed signatures with a `mapping(bytes32 => bool) private _usedConsents` keyed on the full message hash. Mark the hash as used when `allowPushers` writes the remapping, and reject any call that presents an already-used hash. This makes each signed consent single-use, so `revokePusher()` permanently invalidates the delegation regardless of the deadline.

```solidity
mapping(bytes32 => bool) private _usedConsents;

// inside allowPushers, after ECDSA.recover succeeds:
require(!_usedConsents[hash], ConsentAlreadyUsed());
_usedConsents[hash] = true;
namespaceRemapping[pusher] = msg.sender;
```

Alternatively, include a per-pusher nonce in the signed message and increment it on every successful delegation, so old signatures are automatically invalidated.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent with deadline = now + 1 day
uint256 deadline = block.timestamp + 1 days;
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(oracle), deadline, pusher, creator))
);
(uint8 v, bytes32 r, bytes32 s) = vm.sign(PUSHER_KEY, hash);
bytes memory sig = abi.encodePacked(r, s, v);

address[] memory pushers = new address[](1);
pushers[0] = pusher;
bytes[] memory sigs = new bytes[](1);
sigs[0] = sig;

// 2. Creator delegates pusher
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs);
assertEq(oracle.namespaceRemapping(pusher), creator); // delegated

// 3. Pusher revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0)); // cleared

// 4. Creator replays the SAME signature — deadline still valid
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs);           // succeeds, no revert
assertEq(oracle.namespaceRemapping(pusher), creator);   // re-delegated!

// 5. Pusher's own feeds now receive no updates → stale → pool swaps revert with FeedStalled
```

The pusher's `revokePusher()` call at step 3 is completely undone by step 4. The pusher cannot escape the creator's namespace until `block.timestamp > deadline`. [6](#0-5) [2](#0-1)

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

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L214-217)
```text
    function getBidAndAskPrice() external override returns (uint128 bid, uint128 ask) {
        (bid, ask) = _getBidAndAskPrice();
        if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L282-283)
```text
        // Stale reference → not ok. Clamping to a stale anchor is the one false-safety case.
        if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);
```
