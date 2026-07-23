### Title
Pusher Delegation Signature Replay Nullifies `revokePusher()` — (`File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` verifies a pusher's EIP-191 consent signature but never marks it as consumed. Any creator who holds a valid (not-yet-expired) signature can replay it an unlimited number of times to re-establish delegation after the pusher has called `revokePusher()`, permanently nullifying the pusher's revocation within the deadline window and silently redirecting the pusher's subsequent price pushes into the creator's namespace.

---

### Finding Description

`allowPushers` signs over `(chainid, address(this), deadline, pusher, msg.sender)` and checks only that the deadline has not passed: [1](#0-0) 

There is no nonce, no per-signature consumed flag, and no mapping that records which signatures have already been accepted. The contract's own NatSpec acknowledges the risk: [2](#0-1) 

The comment states the deadline prevents re-establishing delegation after revocation, but the deadline only prevents use *after expiry*. Within the deadline window the same bytes can be submitted repeatedly. `revokePusher` clears `namespaceRemapping[msg.sender]` to `address(0)`: [3](#0-2) 

But `allowPushers` unconditionally overwrites it back to `msg.sender` (the creator) on every replay: [4](#0-3) 

`OracleBase._ensureDeadline` is the only guard — it does not track used signatures: [5](#0-4) 

No nonce or consumed-signature mapping exists anywhere in the oracle contracts.

---

### Impact Explanation

The `fallback` push path resolves the effective namespace from `namespaceRemapping[msg.sender]`, falling back to `msg.sender` only when the mapping is zero: [6](#0-5) 

After the creator replays the old signature, every subsequent push the pusher makes — believing it lands in their own namespace — is silently redirected into the creator's namespace. The creator's oracle feeds (keyed by `feedIdOf(creator, slotIndex, positionIndex)`) are updated with the pusher's data without the pusher's ongoing consent. If those feeds are consumed by pools through `AnchoredPriceProvider`, the pool's bid/ask quotes are derived from price data the pusher did not intend to publish to that creator, constituting bad-price execution reaching live swaps. The pusher has no on-chain defense other than waiting for the original deadline to expire.

---

### Likelihood Explanation

The trigger is fully unprivileged from the creator's side: the creator already holds the signature (they received it during the original `allowPushers` call) and can replay it in a single transaction at any time before the deadline. The pusher's `revokePusher` transaction provides no protection. Any creator with a live signature and a motive to keep a pusher's data flowing into their namespace can exploit this.

---

### Recommendation

Track consumed signatures with a `mapping(bytes32 => bool) private _usedSignatures` keyed on the signature hash (or the signed message hash). In `allowPushers`, after recovering the signer, assert `!_usedSignatures[hash]` and then set `_usedSignatures[hash] = true`. This mirrors the standard EIP-2612 nonce pattern and ensures each pusher consent is a one-time, irrevocable grant that cannot be replayed after `revokePusher` clears the mapping.

Alternatively, include a per-pusher nonce in the signed payload and increment it on each successful delegation, making every prior signature immediately invalid.

---

### Proof of Concept

```
1. Creator obtains pusher's signature over
   (chainid, oracle, deadline=T+1day, pusher, creator).

2. Creator calls allowPushers(deadline, [pusher], [sig])
   → namespaceRemapping[pusher] = creator  ✓

3. Pusher calls revokePusher()
   → namespaceRemapping[pusher] = address(0)  ✓ (pusher believes they are free)

4. Creator calls allowPushers(deadline, [pusher], [sig])  ← SAME sig, same deadline
   → _ensureDeadline passes (T < deadline)
   → ECDSA.recover returns pusher  ← no consumed check
   → namespaceRemapping[pusher] = creator  ← revocation silently undone

5. Pusher pushes a price word via fallback, believing it lands in feedIdOf(pusher, …).
   → namespaceRemapping[pusher] == creator → namespace resolves to creator
   → price lands in feedIdOf(creator, …) and is consumed by any pool
      using the creator's AnchoredPriceProvider feed.

Steps 4–5 repeat for the lifetime of the deadline with zero additional cost.
```

### Citations

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
