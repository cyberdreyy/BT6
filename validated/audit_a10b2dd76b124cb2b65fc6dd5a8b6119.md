### Title
`allowPushers` does not guard against re-delegation after pusher self-revocation, rendering `revokePusher()` ineffective within the deadline window — (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.allowPushers()` accepts a pusher's EIP-191 consent signature and writes `namespaceRemapping[pusher] = creator`. It performs no check on whether the pusher has already cleared that mapping via `revokePusher()`. Because the same signed message remains cryptographically valid until its deadline expires, a creator can replay it immediately after the pusher self-revokes, silently restoring the delegation. The function's own NatSpec comment acknowledges the risk but incorrectly claims the deadline alone prevents it.

---

### Finding Description

`allowPushers` signs consent over `(chainid, address(this), deadline, pusher, creator)`. [1](#0-0) 

The NatSpec explicitly warns:

> "The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."

But the deadline only blocks calls made *after* it expires. Within the window the signature is still valid, and `allowPushers` contains no guard that prevents re-use once the pusher has revoked: [2](#0-1) 

`revokePusher()` clears the mapping to `address(0)`: [3](#0-2) 

There is no nonce, no used-signature bitmap, and no `require(namespaceRemapping[pusher] == address(0))` guard in `allowPushers`. The creator already holds the pusher's signature from the original delegation call, so replaying it costs one transaction and zero additional consent from the pusher.

The analog to the Argo "already marked" invariant is exact: just as `mark_vault` failed to assert `marker_addr == @0` before overwriting auction state, `allowPushers` fails to assert the pusher has not already revoked before overwriting `namespaceRemapping`.

---

### Impact Explanation

Every push through `fallback()` resolves the writer's namespace via `namespaceRemapping[msg.sender]`: [4](#0-3) 

If the creator re-establishes the delegation after the pusher revokes, all subsequent pushes from that pusher continue to land in the **creator's** namespace, not the pusher's own. Any pool or `AnchoredPriceProvider` consuming a feed under the creator's namespace will receive prices from a pusher who has explicitly withdrawn consent. If the pusher revoked because their key was compromised or their data became unreliable, the creator's re-delegation forces bad prices into the oracle slot that pools read for bid/ask quotes — a direct bad-price execution path.

---

### Likelihood Explanation

Medium. The creator must actively replay the signature after each revocation. However:
- The creator already possesses the signature (used it for the original `allowPushers` call).
- The creator has a strong economic incentive to maintain the data feed.
- The pusher has no on-chain mechanism to invalidate the signature before the deadline; their only recourse is to stop pushing entirely, making the feed go stale.

---

### Recommendation

Add per-pusher nonce tracking so that each consent signature can only be consumed once:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers, include the nonce in the signed hash:
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender, pusherNonce[pusher]++))
```

Alternatively, after verifying the signature, assert the pusher has not already revoked:

```solidity
require(namespaceRemapping[pusher] == address(0), AlreadyRevoked());
```

Either approach ensures that a pusher's `revokePusher()` call cannot be silently undone by the creator replaying the original consent.

---

### Proof of Concept

```solidity
// Setup: creator signs pusher consent with a 1-year deadline
uint256 deadline = block.timestamp + 365 days;
bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

// Step 1: creator establishes delegation
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator); // delegated

// Step 2: pusher self-revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0)); // revoked

// Step 3: creator replays the SAME signature — no new consent required
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig)); // succeeds

// Step 4: revocation is silently undone
assertEq(oracle.namespaceRemapping(pusher), creator); // delegation restored

// Step 5: pusher's next fallback push lands in creator's namespace, not their own
vm.prank(pusher);
(bool ok,) = address(oracle).call(_wordAt(slotId, pos, raw, tsMs));
assertTrue(ok);
// oracle.getOracleData(feedIdOf(creator, slotId, pos)).price == pusher's price
// oracle.getOracleData(feedIdOf(pusher,  slotId, pos)).price == 0
```

The loop at steps 2–3 can be repeated indefinitely until the deadline expires, making `revokePusher()` a no-op for EOA pushers for the entire lifetime of the signed consent.

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L186-211)
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
