### Title
`revokePusher()` is rendered ineffective by signature replay in `allowPushers` — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

The `allowPushers` function in `CompressedOracleV1` accepts pusher consent signatures that bind only `(chainid, oracle, deadline, pusher, creator)` with no nonce or one-time-use flag. A creator can replay a previously accepted signature — before its deadline — to silently re-establish a delegation that the pusher has already revoked via `revokePusher()`. This makes `revokePusher()` ineffective for the entire lifetime of the original deadline, allowing unauthorized price pushes to continue reaching production feeds and, through them, live pool swaps.

---

### Finding Description

When a pusher grants consent to be delegated into a creator's namespace, the signed payload is:

```solidity
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

There is no nonce, no used-signature bitmap, and no one-time-use flag. `allowPushers` only checks that the deadline has not yet passed and that the recovered signer equals `pusher`. After a pusher calls `revokePusher()`, which sets `namespaceRemapping[pusher] = address(0)`: [2](#0-1) 

the creator can immediately call `allowPushers` again with the identical `(deadline, pusher, signature)` tuple, restoring `namespaceRemapping[pusher] = creator`. The revocation state write is overwritten by the replayed stale credential — the exact structural analog of the external report's cached-pre-value overwrite pattern.

The documentation itself acknowledges the deadline is load-bearing for this reason:

> "the signed consent has no data timestamp, so an undated signature could re-establish a delegation after the pusher revoked it" [3](#0-2) 

No maximum deadline is enforced, so a creator can set `deadline` years in the future, making the replay window arbitrarily long.

---

### Impact Explanation

The `fallback()` push path routes every incoming slot word to `namespaceRemapping[msg.sender]` (falling back to `msg.sender` only when the mapping is zero): [4](#0-3) 

If the pusher's signing key is compromised and the pusher calls `revokePusher()` to protect the creator's feeds, the creator (unaware of the compromise, or acting in bad faith) can replay the original consent signature to restore the delegation. The attacker holding the compromised key then continues to push arbitrary prices into the creator's namespace. Those prices flow through `getOracleData` → `PriceProvider._getBidAndAskPrice` / `AnchoredPriceProvider._readLeg` → `getBidAndAskPrice()` → live pool swap math, causing bad-price execution and direct loss of trader or LP principal. [5](#0-4) 

---

### Likelihood Explanation

- The creator holds the original signature and can replay it at any time before the deadline with a single transaction.
- No maximum deadline is enforced; a deadline set years in the future makes the window permanent for practical purposes.
- The pusher has no on-chain way to invalidate the old signature short of waiting for the deadline to expire.
- The scenario where a pusher's key is compromised and the pusher attempts to self-revoke is a documented and expected use of `revokePusher()`.

---

### Recommendation

Add a per-pusher nonce or a used-signature set to `allowPushers` so each consent signature is accepted exactly once:

```solidity
mapping(bytes32 => bool) private _usedPusherConsents;

function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
    _ensureDeadline(deadline);
    for (uint256 i; i < pushers.length; i++) {
        address pusher = pushers[i];
        if (pusher == msg.sender) revert NoSelfRemapping();

        bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
            keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
        );
        require(!_usedPusherConsents[hash], "consent already used");
        require(pusher == ECDSA.recover(hash, signatures[i]));

        _usedPusherConsents[hash] = true;   // ← invalidate on first use
        namespaceRemapping[pusher] = msg.sender;
        emit PusherAuthorized(pusher, msg.sender);
    }
}
```

Alternatively, include a per-pusher nonce in the signed payload and increment it on each successful `allowPushers` call, so a revoked pusher can increment their own nonce to invalidate any outstanding signatures.

---

### Proof of Concept

```
1. Pusher signs: keccak256(abi.encode(chainid, oracle, deadline=T_far_future, pusher, creator))
   → sig

2. Creator calls allowPushers(T_far_future, [pusher], [sig])
   → namespaceRemapping[pusher] = creator   ✓

3. Pusher's key is compromised. Pusher calls revokePusher()
   → namespaceRemapping[pusher] = address(0)   ✓ (intended protection)

4. Creator calls allowPushers(T_far_future, [pusher], [sig])  ← SAME sig, SAME deadline
   → namespaceRemapping[pusher] = creator   ← revocation silently overwritten

5. Attacker (holding compromised key) calls fallback() with crafted slot word
   → word routes to creator's namespace (not pusher's own namespace)
   → creator's feed updated with attacker-controlled price

6. AnchoredPriceProvider._readLeg(baseFeedId) reads the corrupted price
   → _computeBidAsk produces an attacker-controlled bid/ask
   → MetricOmmPool swap executes at the bad price
   → trader or LP suffers direct principal loss
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L204-207)
```text
            bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
                keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
            );
            require(pusher == ECDSA.recover(hash, signatures[i]));
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

**File:** smart-contracts-poc/contracts/oracles/compressed/docs/en/slot-structure.md (L27-29)
```markdown
Delegation (`allowPushers`) requires each pusher's EIP-191 signature (and a deadline:
the signed consent has no data timestamp, so an undated signature could re-establish a
delegation after the pusher revoked it).
```
