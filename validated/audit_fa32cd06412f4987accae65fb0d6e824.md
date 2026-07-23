### Title
Pusher Revocation Bypass via Signature Replay in `allowPushers` Feeds Bad Prices into Pools — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` accepts an EIP-191 signature that commits to `(chainid, oracle_address, deadline, pusher, creator)` but contains no nonce and no "consumed" flag. The same signature is therefore valid for every call to `allowPushers` until the deadline expires. A pusher who calls `revokePusher()` to clear their delegation can have that revocation silently undone by the creator replaying the original signature, re-writing `namespaceRemapping[pusher] = creator` and redirecting all subsequent fallback pushes back into the creator's namespace without the pusher's knowledge.

---

### Finding Description

`allowPushers` verifies the pusher's EIP-191 consent and then unconditionally overwrites `namespaceRemapping[pusher]`:

```solidity
// CompressedOracle.sol L204-209
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(pusher == ECDSA.recover(hash, signatures[i]));
namespaceRemapping[pusher] = msg.sender;
```

The code's own NatSpec acknowledges the risk:

> *"The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."*

The deadline prevents replay **after** it expires, but it does nothing to prevent replay **before** it expires. There is no nonce, no per-signature consumed mapping, and no check that `namespaceRemapping[pusher]` is currently `address(0)`. A creator who holds the pusher's signed bytes can call `allowPushers` an unlimited number of times within the deadline window.

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

But the creator immediately re-establishes it by replaying the same signature. The pusher has no on-chain mechanism to invalidate a signature before its deadline.

The fallback push path resolves the namespace at call time:

```solidity
// CompressedOracle.sol L315-316
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
```

So every push the pusher makes after believing they have revoked still lands in the creator's namespace, not their own.

---

### Impact Explanation

After a pusher revokes and begins pushing data for a new purpose (e.g., a different asset pair, a different price scale, or a different creator), the creator replays the old signature. The pusher's new data is silently written into the old creator's namespace. Any pool whose `AnchoredPriceProvider` or `PriceProvider` reads from that creator's `feedId` receives the wrong price — a bad-price execution scenario that can cause traders to receive more output than the oracle curve permits or LPs to suffer losses from mispriced swaps.

The compressed oracle is the permissionless price source for pools; a corrupted slot value flows directly through `getOracleData → price → getSellAndBuyPrices → MetricOmmPool.swap`, with no additional sanity check between the oracle read and swap settlement.

---

### Likelihood Explanation

- The creator must have retained the pusher's signed bytes — a normal operational assumption since the creator submitted them on-chain and they are visible in calldata.
- The deadline must not have expired. Deadlines are chosen by the creator at signing time; a creator who anticipates needing long-lived delegation will choose a far-future deadline, maximising the replay window.
- The pusher has no way to detect or prevent the replay short of monitoring every `allowPushers` transaction on-chain.
- The trigger is a single permissionless transaction by the creator — no privileged role required.

---

### Recommendation

Track consumed signatures with a `mapping(bytes32 => bool) private _usedDelegations` keyed on the full message hash, and revert if the hash has already been consumed:

```solidity
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
);
require(!_usedDelegations[hash], "signature already consumed");
require(pusher == ECDSA.recover(hash, signatures[i]));
_usedDelegations[hash] = true;
namespaceRemapping[pusher] = msg.sender;
```

Alternatively, add a per-pusher nonce that the pusher increments on revocation, and include it in the signed message, so any previously issued signature becomes invalid the moment the pusher revokes.

---

### Proof of Concept

1. Creator A calls `allowPushers(deadline=T+30days, [pusher], [sig])` — `namespaceRemapping[pusher] = creatorA`.
2. Pusher calls `revokePusher()` — `namespaceRemapping[pusher] = address(0)`. Pusher begins pushing BTC/USD data into their own namespace for creator B.
3. Creator A calls `allowPushers(deadline=T+30days, [pusher], [sig])` again with the **identical** signature — `namespaceRemapping[pusher] = creatorA` is restored. No revert occurs because `block.timestamp < T+30days` and the signature is cryptographically valid.
4. Pusher's next fallback push (BTC/USD slot 0) resolves `namespaceRemapping[pusher] == creatorA` and writes into `creatorA`'s slot 0.
5. Creator A's pool, which reads `feedIdOf(creatorA, 0, positionIndex)` expecting ETH/USD, now receives BTC/USD prices.
6. Swaps execute at the wrong price; traders or LPs incur direct losses.

Steps 3–6 can be repeated by creator A every time the pusher revokes, for the entire 30-day window, with no on-chain defence available to the pusher. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L311-316)
```text
    fallback() override external {
        uint256 end;
        uint256 namespace;

        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;
```

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L124-126)
```text
    function _ensureDeadline(uint256 deadline) internal view virtual {
        require(block.timestamp <= deadline, DeadlineExceeded());
    }
```
