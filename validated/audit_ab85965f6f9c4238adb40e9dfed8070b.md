### Title
`revokePusher()` Is Ineffective Within the Deadline Window Due to Signature Replay in `allowPushers` — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary

`CompressedOracleV1.revokePusher()` clears `namespaceRemapping[msg.sender]`, but the creator can immediately replay the original `allowPushers` call with the same EIP-191 signature to re-establish the delegation. Because the consent signature contains no nonce and no used-signature tracking exists, `revokePusher()` is a no-op for any pusher whose consent signature has not yet expired. A compromised pusher key that the pusher tried to isolate can continue writing arbitrary prices into the creator's oracle namespace.

### Finding Description

`allowPushers` signs and verifies the tuple `(block.chainid, address(this), deadline, pusher, creator)`: [1](#0-0) 

There is no nonce, no per-pusher revocation counter, and no mapping of consumed signature hashes. The only replay guard is the deadline check: [2](#0-1) 

`revokePusher()` sets `namespaceRemapping[pusher] = address(0)`: [3](#0-2) 

Because the same `(deadline, pusher, creator)` tuple is still valid after revocation, the creator can call `allowPushers` again with the identical signature and deadline, writing `namespaceRemapping[pusher] = creator` again. The code's own NatSpec acknowledges the risk but incorrectly concludes that the deadline alone is sufficient: [4](#0-3) 

The deadline limits the *total* replay window but does not prevent replay *within* that window after a revocation.

### Impact Explanation

A pusher's `fallback()` push lands in the creator's namespace because `namespaceRemapping[msg.sender]` resolves to the creator: [5](#0-4) 

Each accepted word overwrites the entire 256-bit storage slot — all four feed lanes and the shared 56-bit timestamp — with attacker-controlled values: [6](#0-5) 

`getOracleData` decodes the packed slot and returns the attacker-supplied `U64x32` price and codebook spread indices directly to any `PriceProvider` or `AnchoredPriceProvider` that reads the feed: [7](#0-6) 

A bad price reaching a pool swap violates the protocol's Quote Sanity invariant (`0 < bid < ask`) and Swap Conservation invariant, causing traders to receive more than the oracle permits or LPs to receive less than owed.

### Likelihood Explanation

The trigger requires two conditions: (1) a pusher key is compromised, and (2) the creator — or an automated keeper that monitors `namespaceRemapping` and re-establishes delegations to maintain uptime — replays `allowPushers` with the still-valid signature. Production oracle deployments commonly use keeper bots for exactly this kind of liveness maintenance, making condition (2) realistic without any malicious intent from the creator. The pusher signed a consent with a deadline that may be days or weeks in the future, so the replay window is large.

### Recommendation

Track consumed consent signatures with a `mapping(bytes32 => bool) private _usedConsents` keyed on the signature hash, and revert if the same hash is submitted twice. Alternatively, add a per-pusher revocation nonce to the signed payload so that a revocation increments the nonce and invalidates all prior signatures for that pusher/creator pair.

```solidity
// In allowPushers, after signature recovery:
bytes32 sigHash = keccak256(signatures[i]);
require(!_usedConsents[sigHash], "consent already used or revoked");
_usedConsents[sigHash] = true;
namespaceRemapping[pusher] = msg.sender;
```

### Proof of Concept

```
1. Pusher signs: consent = keccak256(abi.encode(chainid, oracle, deadline=T+7days, pusher, creator))
2. Creator calls allowPushers(T+7days, [pusher], [sig])
   → namespaceRemapping[pusher] = creator  ✓
3. Pusher's key is compromised; pusher calls revokePusher()
   → namespaceRemapping[pusher] = address(0)  ✓ (appears safe)
4. Creator (or keeper bot) calls allowPushers(T+7days, [pusher], [sig]) with the SAME sig
   → _ensureDeadline passes (deadline not yet expired)
   → ECDSA.recover returns pusher  ✓
   → namespaceRemapping[pusher] = creator  ← delegation re-established
5. Attacker (holding compromised pusher key) calls oracle.fallback() with crafted slot word:
   price = U64x32.encode(attacker_price), s0/s1 = valid codebook indices, ts = block.timestamp*1000
   → slot overwritten in creator's namespace
6. PriceProvider.getBidAndAskPrice reads the feed → returns attacker_price as mid
7. Pool.swap executes at attacker_price → bad-price execution, LP loss
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L113-117)
```text
        data.price = U64x32.decode(compressed.p);
        data.spread0 = _decodeCodebookIndex(compressed.s0);
        data.spread1 = _decodeCodebookIndex(compressed.s1);
        data.timestampMs = _layout.timestampMs;
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L192-193)
```text
    function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
        _ensureDeadline(deadline);
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L340-343)
```text
            bool newer = timestampMs.isAfter(oldTimestampMs);
            if (!newer) continue;

            _writeStorage(key, bytes32(bytes32(word & ~uint256(0xff))));
```
