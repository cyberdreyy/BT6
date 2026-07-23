### Title
`allowPushers` Delegation Signature Lacks Nonce, Allowing Creator to Replay Consent and Nullify Pusher's `revokePusher()` Within the Deadline Window — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary

`allowPushers` signs consent over `(chainid, oracle, deadline, pusher, creator)` with no nonce. Within the deadline window the creator can replay the identical signature after the pusher has called `revokePusher()`, silently re-establishing the delegation and redirecting every subsequent fallback push back into the creator's namespace. The code comment itself acknowledges the risk but the deadline only closes the window after expiry, not within it.

### Finding Description

`allowPushers` constructs the signed digest as:

```solidity
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

There is no nonce, no used-signature bitmap, and no per-pusher revocation counter. The same `(deadline, pusher, creator)` tuple produces the same hash every time it is presented before `block.timestamp > deadline`.

`revokePusher` clears the mapping:

```solidity
namespaceRemapping[msg.sender] = address(0);
``` [2](#0-1) 

But nothing prevents the creator from immediately calling `allowPushers` again with the original signature (still valid before the deadline) to write `namespaceRemapping[pusher] = creator` back.

The code comment on `allowPushers` explicitly states the deadline is the only guard against post-revocation replay:

> *"the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it."* [3](#0-2) 

The deadline closes the window only after it expires; within the window the guard is absent.

The `fallback` push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [4](#0-3) 

So every push the pusher makes after believing they have revoked lands in the creator's namespace instead of their own, overwriting the creator's live oracle slot with whatever price data the pusher is now producing for a different purpose.

### Impact Explanation

An automated pusher (e.g., a price-feed relay bot) that calls `revokePusher()` and then continues pushing data for its own namespace will unknowingly write into the creator's namespace if the creator replays the consent. The creator's `CompressedOracleV1` slots — which back `AnchoredPriceProvider` reads via `price(feedId, pool)` — will receive the pusher's new, unintended price data. Because the `CompressedOracleV1` is open (no `inSwap` binding, reads are permissionless), any pool or integrator consuming those feeds will execute swaps against the corrupted bid/ask. This satisfies the **bad-price execution** impact: an unbounded or inverted quote reaches a live pool swap. [5](#0-4) 

### Likelihood Explanation

The trigger requires a creator who is willing to replay the signature (malicious or mistaken) and a pusher that is an automated relay that does not re-check `namespaceRemapping` before each push. Both conditions are realistic in production oracle infrastructure where pushers are off-chain bots and creators are protocol deployers. The replay itself costs only gas and requires no special privilege beyond holding the original calldata.

### Recommendation

Add a per-pusher revocation nonce to the signed digest:

```solidity
mapping(address => uint256) public pusherNonce; // incremented on every revoke

// in allowPushers:
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(
        block.chainid, address(this), deadline,
        pusher, msg.sender,
        pusherNonce[pusher]   // <-- binds consent to current revocation epoch
    ))
);

// in revokePusher:
pusherNonce[msg.sender]++;
namespaceRemapping[msg.sender] = address(0);
```

This makes every previously issued consent invalid the moment the pusher revokes, regardless of the deadline.

### Proof of Concept

```
T=0   Pusher P signs: keccak256(chainid, oracle, deadline=T+365d, P, C)  → sig
T=1   Creator C calls allowPushers(T+365d, [P], [sig])
        → namespaceRemapping[P] = C  ✓

T=2   P calls revokePusher()
        → namespaceRemapping[P] = 0  ✓  (P believes it is now pushing to own ns)

T=3   C calls allowPushers(T+365d, [P], [sig])   // SAME sig, deadline still valid
        → namespaceRemapping[P] = C  ← revocation silently undone

T=4   P's relay bot pushes slot word (price=X, slotId=7, ts=now)
        fallback: creator = namespaceRemapping[P] = C  ← not P
        → sstore(C_namespace | 7, word)   // P's data lands in C's oracle slot

T=5   Pool backed by C's feed calls getBidAndAskPrice()
        → AnchoredPriceProvider reads C's slot → price=X (P's unintended value)
        → swap executes at corrupted bid/ask
``` [6](#0-5) [7](#0-6)

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L161-169)
```text
    /// @notice Unified read path shared with the providers oracle. The compressed oracle is open, so
    ///         `pool` is unused (no in-swap binding) and reads are permissionless.
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L326-344)
```text
        for (uint256 ptr = 0; ptr < end; ptr += 32) {
            uint256 word;
            assembly ("memory-safe") {
                word := calldataload(ptr)
            }
            // casting to 'uint8' is safe we want LSB
            // forge-lint: disable-next-line(unsafe-typecast)
            uint8 slotId = uint8(word);
            TimeMs timestampMs = toTimeMs(word >> 8 & X56);
            timestampMs.revertIfAfterBlockTimeWithDrift(MAX_TIME_DRIFT);
            bytes32 key = bytes32(namespace | uint256(slotId));
            uint256 old = uint256(_loadStorage(key));
            TimeMs oldTimestampMs = toTimeMs(old >> 8 & X56);

            bool newer = timestampMs.isAfter(oldTimestampMs);
            if (!newer) continue;

            _writeStorage(key, bytes32(bytes32(word & ~uint256(0xff))));
        }
```
