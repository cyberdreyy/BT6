### Title
`allowPushers` Consent Signature Has No Used-Signature Tracking, Allowing Creator to Replay Pusher's Revoked Consent Within the Deadline Window - (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.allowPushers` accepts a pusher's EIP-191 consent signature and a deadline but never marks the signature as consumed. A creator who saved the original signature can replay it any number of times before the deadline expires, silently re-establishing delegation immediately after the pusher calls `revokePusher()`. The pusher's revocation is therefore ineffective for the entire remaining lifetime of the deadline, and every subsequent fallback push the pusher makes continues to land in the creator's namespace instead of the pusher's own.

---

### Finding Description

`allowPushers` builds the signed digest as:

```
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
```

and verifies the pusher's signature against it, then unconditionally writes `namespaceRemapping[pusher] = msg.sender`. [1](#0-0) 

There is no `usedSignatures` mapping, no per-pusher nonce, and no state change that would make a second call with the identical `(deadline, pusher, signature)` tuple fail. The only guard is `_ensureDeadline(deadline)`, which only rejects calls made *after* the deadline — it does nothing to prevent the same signature from being submitted again *before* the deadline. [2](#0-1) 

`revokePusher()` clears `namespaceRemapping[msg.sender]` to `address(0)`: [3](#0-2) 

But because `allowPushers` has no consumed-signature guard, the creator can immediately call `allowPushers` again with the same signature and restore `namespaceRemapping[pusher] = creator`. The code's own NatSpec comment acknowledges the risk ("an undated signature could re-establish a delegation AFTER the pusher revoked it") but incorrectly claims the deadline solves it — the deadline only prevents replay *after* it expires, not within the remaining window. [4](#0-3) 

---

### Impact Explanation

Every fallback push the pusher makes after their revocation attempt still lands in the creator's namespace because `namespaceRemapping[pusher]` was silently restored: [5](#0-4) 

If the pusher, believing they have revoked, stops pushing data or redirects their data to their own namespace, the creator's namespace receives no further updates. Any pool or `AnchoredPriceProvider` reading from the creator's namespace then consumes a stale price — a direct bad-price execution path. Alternatively, if the pusher continues pushing (unaware the revocation was nullified), their price data flows to a namespace and pool they explicitly tried to exit, violating the delegation-cleanup invariant.

---

### Likelihood Explanation

The trigger is fully unprivileged from the creator's side: the creator is a valid semi-trusted actor who already holds the original signature (they submitted it in the first `allowPushers` call). No special setup is required beyond saving the calldata from the first transaction. The pusher's `revokePusher()` is a public, zero-argument call that any pusher would reasonably use to exit a delegation, making the race condition easy to trigger in practice.

---

### Recommendation

Track consumed signatures. The simplest fix is a `mapping(bytes32 => bool) private _usedConsentSigs` keyed on the digest hash, set to `true` on first acceptance and checked before writing `namespaceRemapping`:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(!_usedConsentSigs[hash], "consent already used");
require(pusher == ECDSA.recover(hash, signatures[i]));
_usedConsentSigs[hash] = true;
namespaceRemapping[pusher] = msg.sender;
```

Alternatively, replace the deadline with a per-pusher nonce (`pusherNonce[pusher]++` on each successful delegation or revocation) and include the nonce in the signed digest, so any previously issued signature is automatically invalidated after a state change.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent for creator with deadline = now + 1 day
uint256 deadline = block.timestamp + 1 days;
bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

// 2. Creator establishes delegation
address[] memory pushers = new address[](1);
pushers[0] = pusher;
bytes[] memory sigs = new bytes[](1);
sigs[0] = sig;
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs);
assertEq(oracle.namespaceRemapping(pusher), creator); // delegated

// 3. Pusher revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0)); // cleared

// 4. Creator replays the SAME signature — succeeds, no revert
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs);
assertEq(oracle.namespaceRemapping(pusher), creator); // re-established!

// 5. Pusher's next fallback push still lands in creator's namespace
uint56 tsMs = uint56(block.timestamp * 1000);
uint48 raw = _packRaw(999_000, 3, 3);
vm.prank(pusher);
(bool ok,) = address(oracle).call(_wordAt(0, 0, raw, tsMs));
assertTrue(ok);
// Creator's namespace updated — pusher's own namespace stays empty
assertEq(oracle.getOracleData(oracle.feedIdOf(creator, 0, 0)).price, U64x32.decode(uint32(raw >> 16)));
assertEq(oracle.getOracleData(oracle.feedIdOf(pusher,  0, 0)).price, 0);
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L315-321)
```text
        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;

        assembly ("memory-safe") {
            end := calldatasize()
            namespace := shl(96, creator) // [creator:20][zeros:12]
        }
```
