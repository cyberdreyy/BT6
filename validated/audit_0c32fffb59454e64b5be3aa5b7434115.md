### Title
Replayable pusher consent signature allows creator to permanently nullify `revokePusher()`, forcing a revoked EOA to continue writing into the creator's oracle namespace — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` verifies a pusher's EIP-191 consent signature but never marks it as consumed. A creator who holds a previously submitted signature can replay it after the pusher calls `revokePusher()`, instantly re-establishing the delegation. Because the signature is bound only to `(chainid, oracle, deadline, pusher, creator)` and carries no nonce or revocation counter, the replay succeeds unconditionally as long as the deadline has not expired. The pusher's self-revocation right is therefore unenforceable for the entire lifetime of the original deadline.

---

### Finding Description

`allowPushers` constructs and verifies the following digest:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

The signed message contains no nonce, no revocation counter, and no single-use flag. The contract's own NatSpec acknowledges the partial problem — "the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it" — and names the deadline as the mitigation. But the deadline only prevents replay *after* it expires; it does nothing to prevent the creator from replaying the same signature *before* expiry, which is the common case when a pusher revokes mid-delegation. [2](#0-1) 

`revokePusher()` clears `namespaceRemapping[msg.sender]` to `address(0)`:

```solidity
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
``` [3](#0-2) 

There is no state change that invalidates the old signature. The creator can immediately call `allowPushers` again with the identical `(deadline, pusher, sig)` tuple retrieved from the original on-chain transaction, and `namespaceRemapping[pusher]` is set back to `creator`. This cycle can be repeated indefinitely until the deadline expires.

The `fallback()` push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

So every push the revoked-but-re-delegated pusher makes continues to overwrite the creator's namespace slots, not the pusher's own namespace.

The `CompressedOracleV1` is the `offchainOracle` consumed by `AnchoredPriceProvider`, which is the standard provider for public pools. `_readLeg` calls `IPricedOracle(address(offchainOracle)).price(feedId, msg.sender)`, which reads directly from the creator's namespace: [5](#0-4) 

---

### Impact Explanation

The pusher's `revokePusher()` self-sovereign right is unenforceable for the full lifetime of the original deadline. The concrete harm path:

1. A pusher's signing key is compromised. The pusher calls `revokePusher()` to stop the damage.
2. The creator replays the original consent signature, re-establishing `namespaceRemapping[pusher] = creator`.
3. The attacker controlling the compromised key continues to push arbitrary price data into the creator's namespace slots.
4. `AnchoredPriceProvider._readLeg` reads those slots and forwards the corrupted mid/spread to `_computeBidAsk`, which produces bid/ask quotes consumed by pool swaps.
5. Traders execute swaps at attacker-controlled prices; the pool receives less input than the oracle curve permits or pays out more than it should.

Even without key compromise, a pusher who legitimately wants to stop providing prices for a creator (e.g., service shutdown, legal reasons) cannot do so until the deadline expires — which may be months or years away if the creator chose a long-lived deadline.

---

### Likelihood Explanation

The replay requires only that the creator saved the original `allowPushers` calldata, which is permanently available in on-chain transaction history. No special privilege, no new signature, no off-chain coordination. The creator calls `allowPushers` again with the same arguments. The attack is O(1) gas and can be front-run against any `revokePusher` transaction in the mempool, making revocation a race the pusher cannot reliably win.

---

### Recommendation

Track consumed signatures with a per-pusher nonce or a `usedSignatures` mapping, and include the nonce in the signed digest:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
uint256 nonce = pusherNonce[pusher]++;
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender, nonce))
);
```

Alternatively, after `revokePusher()` increments a per-pusher revocation counter and the signed message includes that counter, any previously issued signature becomes invalid immediately upon revocation — the same fix the Swafe mitigation applied (`cnt_rec` incremented on acceptance).

---

### Proof of Concept

```solidity
// 1. Pusher signs consent for creator with deadline = block.timestamp + 365 days
bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

// 2. Creator establishes delegation
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator); // delegated

// 3. Pusher revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0)); // revoked

// 4. Creator replays the SAME signature — no revert, delegation re-established
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator); // ← revocation nullified

// 5. Pusher's next fallback push lands in creator's namespace, not pusher's own
uint56 tsMs = uint56(block.timestamp * 1000);
vm.prank(pusher);
(bool ok,) = address(oracle).call(_wordAt(0, 0, _packRaw(ATTACKER_PRICE, 4, 4), tsMs));
assertTrue(ok);
// creator's feed now holds ATTACKER_PRICE, consumed by AnchoredPriceProvider → pool swap
```

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

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L280-280)
```text
        (mid, spreadBps, , refTime) = IPricedOracle(address(offchainOracle)).price(feedId, msg.sender);
```
