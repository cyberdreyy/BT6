### Title
`allowPushers` signed delegation is replayable within the deadline window after `revokePusher()`, silently re-hijacking a pusher's namespace — (`File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` contains no nonce or one-time-use guard on the EIP-191 signature. A creator who holds a valid (non-expired) signed delegation can call `allowPushers` repeatedly with the same signature. Critically, after a pusher calls `revokePusher()` to self-revoke, the creator can immediately replay the original signature and re-write `namespaceRemapping[pusher] = creator`, silently undoing the revocation. The pusher then continues pushing prices believing they are writing to their own namespace, while every push is actually stored in the creator's namespace — feeding the creator's pool feeds with prices the pusher never intended for them.

---

### Finding Description

`allowPushers` builds and verifies a signed hash over `(chainid, address(this), deadline, pusher, msg.sender)`: [1](#0-0) 

There is no nonce, no per-pusher invalidation counter, and no "used-signature" set. The only replay barrier is the deadline: once `block.timestamp > deadline` the signature is dead. Within the deadline window the identical `(deadline, signature)` pair can be submitted an unlimited number of times.

`revokePusher` zeroes the mapping: [2](#0-1) 

But nothing invalidates the original signed message. The creator immediately calls `allowPushers` again with the same calldata, and line 209 re-writes `namespaceRemapping[pusher] = msg.sender`. The revocation is silently undone.

The `fallback()` push path resolves the effective namespace at call time: [3](#0-2) 

So every subsequent push from the pusher — who believes they are writing to their own namespace — is stored under the creator's namespace key and is consumed by the creator's pool feeds.

The code comment on `allowPushers` explicitly acknowledges the risk ("an undated signature could re-establish a delegation AFTER the pusher revoked it") and names the deadline as the mitigation, but the deadline only closes the window after expiry; it does not prevent replay within the window. [4](#0-3) 

---

### Impact Explanation

After the silent re-delegation, the pusher's price words are written into the creator's slot storage. Any pool that reads `price(feedId, pool)` where `feedId` encodes the creator's address will receive prices the pusher intended for a completely different feed. This satisfies the **bad-price execution** impact gate: an unclamped or asset-mismatched bid/ask quote reaches a pool swap, enabling traders to extract value at the expense of LPs or causing the pool to settle at a wrong rate.

---

### Likelihood Explanation

The trigger requires only that the creator retains the original `(deadline, signatures[])` calldata and that the deadline has not yet expired. No privileged role is needed beyond being the creator who originally called `allowPushers`. The pusher's revocation gives no on-chain signal that the signature is spent, so the creator can replay it in the same block as the revocation. Likelihood is **medium**: the creator must act adversarially, but the mechanism is trivially executable with no additional resources.

---

### Recommendation

Maintain a per-pusher nonce or a `mapping(bytes32 => bool) usedSignatures` set. Include the nonce (or a unique salt) in the signed hash and increment/mark it on first use. Alternatively, record the block number or timestamp of the most recent successful `allowPushers` call for each pusher and reject any signature whose embedded deadline predates that record, so a revocation permanently invalidates all prior signatures.

---

### Proof of Concept

```
1. Pusher signs: keccak256(abi.encode(chainid, oracle, deadline=T, pusher, creatorA))
2. CreatorA calls allowPushers(T, [pusher], [sig])
   → namespaceRemapping[pusher] = creatorA  ✓
3. Pusher calls revokePusher()
   → namespaceRemapping[pusher] = address(0)  (pusher believes delegation is dead)
4. CreatorA calls allowPushers(T, [pusher], [sig])  // same calldata, T not yet expired
   → namespaceRemapping[pusher] = creatorA  ✓  (revocation silently undone)
5. Pusher pushes ETH/USD price word intended for their own namespace
   → fallback() resolves creator = creatorA
   → price stored in creatorA's slot
   → creatorA's pool reads wrong asset price → bad-price swap execution
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-317)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;

```
