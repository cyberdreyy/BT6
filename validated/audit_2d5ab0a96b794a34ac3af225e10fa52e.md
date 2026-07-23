### Title
Pusher Consent Signature Replayable Within Deadline Window Allows Creator to Silently Re-Establish Revoked Delegation — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers()` in `CompressedOracle.sol` verifies a pusher's EIP-191 consent signature but never marks it as consumed. After a pusher calls `revokePusher()`, the creator can replay the original consent signature (unchanged, same `deadline`) to silently re-establish the delegation without the pusher's knowledge or fresh consent, as long as `block.timestamp < deadline`.

---

### Finding Description

The `allowPushers` function verifies a pusher's EIP-191 signature that commits to `(block.chainid, address(this), deadline, pusher, msg.sender)`: [1](#0-0) 

There is no nonce, no `usedSignatures` mapping, and no check that the pusher's current `namespaceRemapping` state is `address(0)` (i.e., already revoked). The only guard is `_ensureDeadline(deadline)`, which only checks `block.timestamp < deadline`. [2](#0-1) 

After `revokePusher()` clears `namespaceRemapping[msg.sender] = address(0)`, the creator can immediately call `allowPushers` again with the identical `(deadline, pushers, signatures)` tuple — the signature is still valid, the deadline has not expired, and the function unconditionally writes `namespaceRemapping[pusher] = msg.sender` again.

The code's own NatSpec comment explicitly states the deadline is the mechanism that prevents re-establishment after revocation: [3](#0-2) 

But the deadline only bounds the window — it does not prevent replay within that window. The stated invariant is broken.

**Analog to the external bug:** Just as `last_total_shares_minted` is never decremented after each refund (leaving a stale value that corrupts subsequent calculations), the consent signature is never "consumed" after being applied. It remains reusable until expiry, so a pusher's revocation can be silently undone by replaying the same stale authorization.

---

### Impact Explanation

After a pusher revokes (e.g., because their signing key was compromised), the creator replays the original consent. The pusher's subsequent pushes — which the pusher believes are landing in their own namespace — are silently redirected into the creator's namespace. Any pool whose `PriceProvider` reads from the creator's compressed-oracle feeds then receives prices from a pusher who believes they have revoked, including prices pushed by a compromised key. This is a direct bad-price execution path: stale, manipulated, or attacker-controlled prices reach the pool's bid/ask quote.

The `fallback()` push path resolves the namespace at call time: [4](#0-3) 

So every push after the silent re-delegation lands in the creator's namespace, not the pusher's own, with no on-chain signal to the pusher.

---

### Likelihood Explanation

- The creator already possesses the original consent signature (it was submitted on-chain in the first `allowPushers` call and is trivially recoverable from transaction history).
- The attack window is the entire remaining lifetime of the deadline — which can be set to days or longer.
- The pusher has no way to detect the re-delegation without actively monitoring `PusherAuthorized` events.
- The creator is a semi-trusted actor (not a protocol admin), making this an unprivileged trigger within the contest's allowed scope.

---

### Recommendation

Track consumed signatures with a `mapping(bytes32 => bool) public usedConsents` keyed on the signature hash, and revert if the same hash is submitted twice:

```solidity
mapping(bytes32 => bool) public usedConsents;

// inside allowPushers, after computing `hash`:
bytes32 consentKey = keccak256(abi.encode(hash, signatures[i]));
require(!usedConsents[consentKey], "consent already used");
usedConsents[consentKey] = true;
```

Alternatively, include a per-pusher nonce in the signed message so each consent is single-use by construction.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent with deadline = now + 1 day
uint256 deadline = block.timestamp + 1 days;
bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

// 2. Creator establishes delegation
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator); // delegated

// 3. Pusher revokes — believes they are now pushing into own namespace
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0)); // cleared

// 4. Creator replays the SAME signature — no revert, delegation restored
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig)); // ← replay
assertEq(oracle.namespaceRemapping(pusher), creator);   // silently re-delegated

// 5. Pusher pushes, believing it lands in own namespace
uint56 tsMs = uint56(block.timestamp * 1000);
uint48 raw  = _packRaw(BAD_PRICE, 5, 0);
vm.prank(pusher);
(bool ok,) = address(oracle).call(_wordAt(2, 3, raw, tsMs));
assertTrue(ok);

// Price lands in CREATOR namespace, not pusher's own
assertEq(oracle.getOracleData(oracle.feedIdOf(creator, 2, 3)).price,
         U64x32.decode(uint32(raw >> 16))); // bad price in creator feed
assertEq(oracle.getOracleData(oracle.feedIdOf(pusher,  2, 3)).price, 0);
// Pool reading creator's feed now receives BAD_PRICE
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L236-243)
```text
    /// @notice Allows a pusher to self-revoke their delegation. After revocation the
    ///         wallet pushes into its OWN namespace again (the registrationless default).
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
