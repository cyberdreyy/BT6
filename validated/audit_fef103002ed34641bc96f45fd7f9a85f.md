### Title
`revokePusher()` self-revocation is bypassable via signature replay — creator can re-establish delegation within the deadline window, allowing a compromised pusher key to continue writing bad prices into production feeds - (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` in `CompressedOracleV1` contains no used-signature tracking and no per-pusher nonce. After a pusher calls `revokePusher()` to stop a compromised key from writing into the creator's namespace, the creator (or an automated relay) can immediately replay the original EIP-191 consent signature — same `deadline`, same `pusher`, same `creator` — to re-establish `namespaceRemapping[pusher] = creator`. The pusher's self-revocation is therefore ineffective for the entire remaining deadline window, and the compromised key continues to write arbitrary prices into the creator's namespace, which downstream price providers and pools consume.

---

### Finding Description

`allowPushers` signs consent as:

```
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

The only replay guard is `_ensureDeadline(deadline)`, which rejects calls after the deadline but accepts any number of calls before it. There is no nonce, no `usedSignatures` bitmap, and no mechanism that invalidates a signature when the pusher revokes. [2](#0-1) 

`revokePusher()` clears `namespaceRemapping[msg.sender] = address(0)`: [3](#0-2) 

But nothing prevents the creator from immediately calling `allowPushers` again with the identical `(deadline, pusher, sig)` tuple, restoring `namespaceRemapping[pusher] = creator`. The code comment itself acknowledges the risk but misidentifies the deadline as the fix:

> "The deadline is likewise required: the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it." [4](#0-3) 

The deadline prevents re-establishment only *after* it expires. During the entire window `[revoke_time, deadline]` — which can be days — the creator can replay the original signature an unlimited number of times, nullifying every `revokePusher()` call the pusher makes.

The `fallback` push path resolves the namespace at call time:

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [5](#0-4) 

So once the mapping is restored, every subsequent `fallback` call from the compromised pusher key writes into the creator's namespace. `getOracleData` decodes those writes and returns them as live prices: [6](#0-5) 

Price providers (`ProtectedPriceProvider`, `PriceProvider`) consume those values via `price(feedId, pool)` and pass them to pool swaps: [7](#0-6) 

---

### Impact Explanation

A compromised pusher key can push arbitrary `(price, spread0, spread1, timestamp)` values into the creator's namespace. Any pool whose price provider reads from that namespace will execute swaps at the attacker-controlled price. Traders receive more output than the oracle curve permits (swap conservation failure) or LPs suffer direct principal loss from mispriced swaps. The `priceGuard` is the only downstream check, but it is set by the creator and may be wide or absent.

This is a **bad-price execution** impact: an attacker-controlled bid/ask quote reaches a live pool swap, causing direct loss of user principal above Sherlock thresholds.

---

### Likelihood Explanation

- Pusher key compromise is a realistic operational risk (hot-wallet key exposure, CI/CD secret leak).
- Creators commonly run automated relay services that re-establish delegation whenever `namespaceRemapping` is cleared — this is the normal operational pattern for keeping pushers active.
- The attacker needs only to wait for the automated relay to replay the signature (one block) after the pusher revokes.
- No privileged access is required: `allowPushers` is a public function callable by any address holding the original signature.

---

### Recommendation

Track consumed signatures to make each consent one-time-use:

```solidity
mapping(bytes32 => bool) public usedConsentSignatures;

function allowPushers(...) external {
    ...
    bytes32 sigHash = keccak256(signatures[i]);
    require(!usedConsentSignatures[sigHash], "signature already used");
    usedConsentSignatures[sigHash] = true;
    namespaceRemapping[pusher] = msg.sender;
}
```

Alternatively, add a per-pusher revocation nonce that the pusher can increment, and include it in the signed digest so that any previously issued signature becomes invalid after revocation.

---

### Proof of Concept

```
T=0:   Pusher signs: sig = sign(chainid, oracle, deadline=T+1day, pusher, creator)
T=1:   Creator calls allowPushers(T+1day, [pusher], [sig])
         → namespaceRemapping[pusher] = creator  ✓

T=2:   Pusher's key is compromised.
T=3:   Pusher calls revokePusher()
         → namespaceRemapping[pusher] = address(0)  ✓ (revocation succeeds)

T=4:   Creator's automated relay detects namespaceRemapping[pusher] == 0,
       calls allowPushers(T+1day, [pusher], [sig])  ← SAME sig, deadline not expired
         → namespaceRemapping[pusher] = creator  ✗ (revocation nullified)

T=5:   Attacker (holding compromised pusher key) calls fallback with
       crafted slot word: price=MAX, spread0=1, spread1=1, timestamp=now
         → _writeStorage(bytes32(creatorNamespace | slotId), craftedWord)

T=6:   Pool swap calls provider.getBidAndAskPrice()
         → oracle.price(feedId, pool) → getOracleData(feedId)
         → returns attacker-controlled price
         → pool executes swap at bad price → trader/LP loss
```

The `revokePusher` → `allowPushers` replay cycle can repeat indefinitely until the deadline expires, making the pusher's self-revocation completely ineffective during the consent window.

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L101-117)
```text
    function getOracleData(bytes32 feedId) public view override returns (OracleData memory data) {
        (address creator, uint8 slotIndex, uint8 positionIndex) = _unpackFeedId(feedId);

        SlotLayout memory _layout = _loadSlotLayout(_oracleSlot(creator, slotIndex));
        CompressedOracleData memory compressed = _selectCompressedData(_layout, positionIndex);

        if (compressed.s1 == 0xff && compressed.s0 == 0xff) {
            data.spread1 = BPS_BASE;
            data.spread0 = BPS_BASE;
            return data;
        }

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

**File:** smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol (L196-199)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        (uint256 mid, uint256 spread, , uint256 refTime) =
            IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender);
        return _computeBidAsk(mid, spread, refTime);
```
