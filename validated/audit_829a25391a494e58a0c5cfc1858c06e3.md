I have a complete picture of the codebase. Let me now deliver the finding.

---

### Title
`allowPushers` Signed Consent Has No Nonce, Letting Creator Replay It to Nullify Pusher Self-Revocation Within the Deadline Window — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary

`CompressedOracleV1.allowPushers` accepts a pusher's EIP-191 signed consent and writes `namespaceRemapping[pusher] = creator`. The signed message is bound to `(chainid, oracle, deadline, pusher, creator)` but carries **no nonce and no single-use flag**. After a pusher calls `revokePusher()` to clear their delegation, the creator can immediately replay the original signature — while the deadline is still valid — to re-establish the mapping. The pusher's self-revocation is therefore completely ineffective for the entire remaining lifetime of the signed consent, and every subsequent push by the pusher continues to land in the creator's namespace.

### Finding Description

`allowPushers` performs two checks before writing `namespaceRemapping[pusher] = msg.sender`:

1. `_ensureDeadline(deadline)` — deadline must be in the future.
2. ECDSA recovery — the recovered signer must equal `pusher`. [1](#0-0) 

Neither check prevents the **same valid signature from being submitted a second time**. There is no `mapping(bytes32 => bool) usedSignatures`, no per-pusher nonce, and no state transition that marks the consent as consumed.

The code's own NatSpec acknowledges the concern:

> *"the deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [2](#0-1) 

The deadline is intended to bound the replay window, but it does **not** prevent replay within that window. A creator who holds the pusher's signature can call `allowPushers` an unlimited number of times before the deadline expires, each time overwriting the `address(0)` that `revokePusher` wrote.

`revokePusher` clears the mapping unconditionally: [3](#0-2) 

But `allowPushers` writes it back without any guard against re-use: [4](#0-3) 

The `fallback()` push path resolves the namespace at call time: [5](#0-4) 

So every push the pusher makes after the creator's replay lands in the creator's namespace, not the pusher's own.

### Impact Explanation

A creator who has obtained a pusher's signed consent (deadline = D) can:

1. Call `allowPushers` → `namespaceRemapping[pusher] = creator`.
2. Wait for the pusher to call `revokePusher()` → `namespaceRemapping[pusher] = address(0)`.
3. Immediately call `allowPushers` again with the **identical signature** → `namespaceRemapping[pusher] = creator` again.
4. Repeat step 3 indefinitely until `block.timestamp > D`.

All pushes the pusher makes during this window — believing they are writing to their own namespace — are silently redirected into the creator's namespace. Any pool that uses the creator's compressed-oracle feed as its price provider will consume these prices. If the pusher is pushing stale, manipulated, or otherwise bad data (or if the creator is the one who wants to keep consuming the pusher's data against the pusher's will), the pool's bid/ask quotes are corrupted, enabling bad-price execution on every swap that reads that feed.

### Likelihood Explanation

- The creator already holds the signature (they submitted it in the original `allowPushers` call).
- The deadline is a caller-chosen `uint256`; real deployments use multi-day or multi-week windows, giving the creator ample time to replay.
- The pusher has no on-chain way to invalidate the signature short of waiting for the deadline to expire; they can only observe `namespaceRemapping` and stop pushing, but an automated pusher bot may not detect the re-delegation.
- No privileged role is required; the creator is a normal EOA.

### Recommendation

Mark each consent signature as consumed after its first use. The simplest fix is a `mapping(bytes32 => bool)` keyed on the signed hash:

```solidity
mapping(bytes32 => bool) private _usedConsentHashes;

function allowPushers(...) external {
    _ensureDeadline(deadline);
    ...
    bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
        keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
    );
    require(!_usedConsentHashes[hash], "consent already used");
    require(pusher == ECDSA.recover(hash, signatures[i]));
    _usedConsentHashes[hash] = true;
    namespaceRemapping[pusher] = msg.sender;
    ...
}
```

Alternatively, include a per-pusher nonce in the signed message and increment it on each successful `allowPushers` call, so any previously issued signature becomes invalid after the first use.

### Proof of Concept

```solidity
// Setup: pusher signs consent for creator, deadline = now + 1 day
uint256 deadline = block.timestamp + 1 days;
bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

address[] memory pushers = new address[](1);
pushers[0] = pusher;
bytes[] memory sigs = new bytes[](1);
sigs[0] = sig;

// Step 1: creator establishes delegation
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs);
assertEq(oracle.namespaceRemapping(pusher), creator);

// Step 2: pusher self-revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0)); // cleared

// Step 3: creator replays the SAME signature — no revert
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs);           // identical call
assertEq(oracle.namespaceRemapping(pusher), creator);   // delegation restored!

// Step 4: pusher's next push (believing it goes to own namespace) lands in creator's
uint56 tsMs = uint56(block.timestamp * 1000);
uint48 raw  = _packRaw(BAD_PRICE, 0, 0);
vm.prank(pusher);
(bool ok,) = address(oracle).call(_wordAt(0, 0, raw, tsMs));
assertTrue(ok);

// BAD_PRICE is now in the creator's namespace, not the pusher's
assertEq(
    oracle.getOracleData(oracle.feedIdOf(creator, 0, 0)).price,
    U64x32.decode(uint32(raw >> 16))   // creator's feed poisoned
);
assertEq(
    oracle.getOracleData(oracle.feedIdOf(pusher, 0, 0)).price,
    0                                   // pusher's own namespace untouched
);
// Any pool reading feedIdOf(creator, 0, 0) now executes swaps at BAD_PRICE.
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
