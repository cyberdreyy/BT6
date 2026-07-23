### Title
Pusher consent signature has no nonce, allowing creator to replay it and silently re-establish a delegation the pusher has revoked — (`File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` verifies a pusher's EIP-191 consent signature but does not invalidate it after first use. Because the signed message contains no nonce or unique identifier, a creator can call `allowPushers` a second time with the identical signature to re-establish a delegation that the pusher already revoked via `revokePusher()`. The pusher's subsequent fallback pushes are silently redirected into the creator's namespace, feeding unintended price data into every pool that reads from that namespace.

---

### Finding Description

`allowPushers` constructs the signed digest as:

```solidity
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

The five fields bound the signature to a specific chain, oracle contract, deadline, pusher, and creator. There is no nonce, no per-use invalidation map, and no check that `namespaceRemapping[pusher]` is currently `address(0)` before writing. The only expiry mechanism is the deadline itself.

`revokePusher` clears the mapping:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [2](#0-1) 

But `allowPushers` unconditionally overwrites it on every valid call:

```solidity
namespaceRemapping[pusher] = msg.sender;
``` [3](#0-2) 

Because the signature is still cryptographically valid until `deadline` expires, the creator can call `allowPushers` again with the same `(deadline, pusher, signature)` tuple immediately after the pusher revokes, restoring `namespaceRemapping[pusher] = creator` without any new consent from the pusher.

The `fallback` push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

So every push the pusher makes after believing they have revoked still lands in the creator's namespace, not their own.

The code comment itself acknowledges the deadline is the only replay barrier:

> "the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it." [5](#0-4) 

The comment treats the deadline as sufficient protection, but the deadline only prevents replay *after* it expires — it does nothing to prevent replay *within* the deadline window, which is exactly when revocation is most needed.

---

### Impact Explanation

The corrupted on-chain value is `namespaceRemapping[pusher]`, which is restored to `creator` without the pusher's knowledge. Every subsequent fallback push from the pusher writes into the creator's storage slots. Pools that use a `PriceProvider` backed by the creator's `CompressedOracleV1` feeds will consume this unintended price data on the next swap. The `price()` read path in `CompressedOracleV1` is permissionless (no `inSwap` binding), so any pool pointing at the creator's feedId will immediately receive the corrupted quote. [6](#0-5) 

A pusher who has revoked may be pushing data for a completely different price pair, a test environment, or a different creator — all of which would overwrite the creator's live production feeds and cause bad-price execution for swappers.

---

### Likelihood Explanation

The trigger requires only that the creator retain the original `(deadline, pusher, signature)` tuple and call `allowPushers` again after the pusher revokes. This is a single permissionless transaction with no special privilege. The deadline window is chosen by the creator at delegation time and can be set to days or weeks, giving a large replay window. The pusher has no on-chain mechanism to detect or prevent the re-establishment.

---

### Recommendation

Track used signatures or increment a per-pusher nonce that is included in the signed digest. The simplest fix is a `mapping(bytes32 => bool) private usedConsents` that marks each digest as consumed on first use:

```solidity
mapping(bytes32 => bool) private usedConsents;

// inside allowPushers, after recovering the signer:
bytes32 digest = keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender));
require(!usedConsents[digest], "consent already used");
usedConsents[digest] = true;
```

Alternatively, include a per-pusher nonce in the signed message and increment it on every successful `allowPushers` call, so a revoked pusher can ensure any future delegation requires a fresh signature.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent for creator with deadline = now + 1 day
uint256 deadline = block.timestamp + 1 days;
bytes32 digest = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(oracle), deadline, pusher, creator))
);
(uint8 v, bytes32 r, bytes32 s) = vm.sign(PUSHER_KEY, digest);
bytes memory sig = abi.encodePacked(r, s, v);

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
assertEq(oracle.namespaceRemapping(pusher), address(0)); // revoked

// 4. Creator replays the SAME signature — no new consent from pusher
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs); // succeeds: same sig, same deadline
assertEq(oracle.namespaceRemapping(pusher), creator); // delegation silently restored

// 5. Pusher's next push (intended for own namespace) lands in creator's namespace
uint56 tsMs = uint56(block.timestamp * 1000);
uint48 raw = _packRaw(9_999_999, 0, 0); // attacker-controlled price
vm.prank(pusher);
(bool ok,) = address(oracle).call(_wordAt(0, 0, raw, tsMs));
assertTrue(ok);

// Creator's feed now contains the pusher's unintended data
IOffchainOracle.OracleData memory data = oracle.getOracleData(oracle.feedIdOf(creator, 0, 0));
assertEq(data.price, U64x32.decode(uint32(raw >> 16))); // bad price in creator namespace
assertEq(oracle.getOracleData(oracle.feedIdOf(pusher, 0, 0)).price, 0); // pusher's own ns empty
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L163-168)
```text
    function price(bytes32 feedId, address /* pool */)
        external
        view
        returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
    {
        return _price(feedId);
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L186-191)
```text
    /// @notice Delegates pusher wallets into the caller's namespace. The pusher's EIP-191
    ///         signature is REQUIRED — without it anyone could remap a foreign pusher
    ///         wallet into their own namespace and silently swallow its pushes. The
    ///         deadline is likewise required: the signed consent carries no timestamp of
    ///         its own, so an undated signature could re-establish a delegation AFTER the
    ///         pusher revoked it.
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L204-207)
```text
            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L209-210)
```text
            namespaceRemapping[pusher] = msg.sender;
            emit PusherAuthorized(pusher, msg.sender);
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
