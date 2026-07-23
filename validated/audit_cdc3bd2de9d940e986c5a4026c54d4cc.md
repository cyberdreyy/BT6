### Title
Pusher Consent Signature Replay Nullifies `revokePusher()` Within Deadline Window — (`File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary

`CompressedOracleV1::allowPushers` signs consent as `keccak256(abi.encode(chainid, address(this), deadline, pusher, creator))` with no per-delegation nonce or invalidation flag. After a pusher calls `revokePusher()`, the creator can immediately replay the original consent signature (while `block.timestamp < deadline`) to re-establish `namespaceRemapping[pusher] = creator`. The pusher's revocation is rendered permanently ineffective until the deadline expires, and any attacker holding the pusher's key continues writing into the creator's oracle namespace — feeding bad prices into every pool that reads from it.

### Finding Description

`allowPushers` constructs the signed digest as:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
``` [1](#0-0) 

The signed message contains no nonce, no revocation counter, and no "used" flag. The only replay guard is the `deadline` field, which the code comment itself acknowledges is the sole protection against re-establishing a delegation after revocation:

> *"the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [2](#0-1) 

`revokePusher()` clears `namespaceRemapping[msg.sender]` to `address(0)`:

```solidity
function revokePusher() external {
    address creator = namespaceRemapping[msg.sender];
    if (creator == address(0) || creator == msg.sender) revert NoSelfRemapping();
    namespaceRemapping[msg.sender] = address(0);
    emit PusherRevoked(msg.sender, creator);
}
``` [3](#0-2) 

But because the original consent signature remains valid until `deadline`, the creator can call `allowPushers` again with the identical `(deadline, [pusher], [sig])` arguments and restore `namespaceRemapping[pusher] = creator` in the very next block. The pusher has no on-chain mechanism to permanently invalidate the signature before the deadline expires.

The `fallback()` push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

So every push the compromised pusher key makes after the creator's replay lands in the creator's namespace, not the pusher's own namespace.

### Impact Explanation

A pool that uses the creator's `CompressedOracleV1` namespace as its price source will receive the bad prices pushed by the attacker holding the compromised pusher key. The `price()` / `getOracleData()` read path is permissionless and has no in-swap binding guard on the compressed oracle:

```solidity
function price(bytes32 feedId, address /* pool */)
    external view
    returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
{
    return _price(feedId);
}
``` [5](#0-4) 

Bad prices (attacker-controlled `U64x32` mid, spread0, spread1) flow directly into any `AnchoredPriceProvider` or pool that reads from the creator's feed IDs, satisfying the **bad-price execution** impact gate.

### Likelihood Explanation

**Low.** Requires: (1) a pusher whose key is compromised or who legitimately wants to stop, (2) a creator who actively replays the signature to prevent revocation. The creator is classified as semi-trusted/bounded in the protocol's security model, making this a bounded but reachable trigger.

### Recommendation

Add a per-pusher revocation nonce or a used-signature registry. The simplest fix is a `mapping(address => uint256) public pusherNonce` incremented on each successful `allowPushers` call and included in the signed digest:

```solidity
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender, ++pusherNonce[pusher]))
```

Alternatively, maintain a `mapping(bytes32 => bool) private _usedConsents` keyed on the full digest and revert if the digest has already been consumed. Either approach ensures that `revokePusher()` permanently invalidates the specific consent that established the delegation.

### Proof of Concept

```solidity
// 1. Pusher signs consent for creator with deadline = now + 1 day
bytes memory sig = sign(PUSHER_KEY, keccak256(abi.encode(
    block.chainid, address(oracle), deadline, pusher, creator
)));

// 2. Creator establishes delegation
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig));
assertEq(oracle.namespaceRemapping(pusher), creator); // delegated

// 3. Pusher revokes (e.g., key compromise detected)
vm.prank(pusher);
oracle.revokePusher();
assertEq(oracle.namespaceRemapping(pusher), address(0)); // revoked

// 4. Creator replays the SAME signature — revocation undone
vm.prank(creator);
oracle.allowPushers(deadline, _arr(pusher), _arr(sig)); // succeeds, no revert
assertEq(oracle.namespaceRemapping(pusher), creator);   // delegation restored

// 5. Attacker (holding pusher key) pushes bad price into creator's namespace
vm.prank(pusher); // attacker controls this key
(bool ok,) = address(oracle).call(encodeBadPrice(slotId, badPrice, tsMs));
assertTrue(ok);

// 6. Bad price is now live in creator's namespace, readable by pools
IOffchainOracle.OracleData memory data = oracle.getOracleData(
    oracle.feedIdOf(creator, slotId, pos)
);
assertEq(data.price, badPrice); // attacker-controlled price reaches pool
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L163-169)
```text
    function price(bytes32 feedId, address /* pool */)
        external
        view
        returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
    {
        return _price(feedId);
    }
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L204-210)
```text
            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));

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
