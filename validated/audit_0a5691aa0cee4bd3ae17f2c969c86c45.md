### Title
`allowPushers` Signature Replay Lets Creator Re-Establish a Revoked Pusher Delegation — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`CompressedOracleV1.allowPushers` signs consent with `(chainid, oracle, deadline, pusher, creator)` but includes **no nonce**. After a pusher calls `revokePusher()` to exit a creator's namespace, the creator can replay the original, still-valid signature to silently re-establish the delegation. The pusher's safety exit is therefore not final, and a compromised pusher key can continue writing bad prices into the creator's feed namespace — prices that downstream pools consume directly.

---

### Finding Description

`allowPushers` requires a pusher's EIP-191 signature over:

```
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

There is no nonce, no per-delegation counter, and no on-chain record that the signature was consumed. The code's own comment acknowledges the partial risk:

> *"the signed consent carries no timestamp of its own, so an undated signature could re-establish a delegation AFTER the pusher revoked it"* [2](#0-1) 

The deadline is offered as the mitigation, but it only bounds the replay window — it does not prevent replay **within** that window. `revokePusher()` sets `namespaceRemapping[pusher] = address(0)`: [3](#0-2) 

After revocation the creator can immediately call `allowPushers` again with the identical `(deadline, pusher, sig)` tuple, writing `namespaceRemapping[pusher] = creator` again: [4](#0-3) 

The `fallback()` push path resolves the effective namespace from `namespaceRemapping[msg.sender]`, so every subsequent push from the re-delegated key lands in the creator's slot, not the pusher's own namespace: [5](#0-4) 

Note: `allowContractPushers` does **not** share this flaw because it re-checks the live `isPusher()` return value on every call. [6](#0-5) 

---

### Impact Explanation

A pool's price provider reads `getOracleData(feedId)` which decodes the slot written by the pusher: [7](#0-6) 

If a pusher's key is compromised and the creator (deliberately or via an automated re-delegation script) replays the original signature after the pusher's `revokePusher()`, the compromised key regains write access to the creator's feed. It can push any `(price, spread0, spread1, timestampMs)` tuple that passes the monotonicity check, injecting a bad bid/ask into every pool that consumes that feed. This is a direct bad-price execution path: stale, inverted, or unbounded prices reach pool swaps.

---

### Likelihood Explanation

- Signatures are commonly stored off-chain by creators for operational re-use (e.g., automated delegation management scripts).
- Deadlines are typically set far in the future (days to years) to avoid operational friction.
- A pusher revoking for security reasons (key compromise) is a realistic scenario.
- The creator does not need to be malicious — an automated system that re-establishes delegations on any revocation event is sufficient.

---

### Recommendation

Record a per-pusher nonce or a consumed-signature bitmap on-chain and include it in the signed digest:

```solidity
mapping(address => uint256) public pusherNonce;

// In allowPushers:
uint256 nonce = pusherNonce[pusher]++;
bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
    keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender, nonce))
);
```

Alternatively, record the signature hash as spent after first use:

```solidity
mapping(bytes32 => bool) public usedSignatures;
// ...
require(!usedSignatures[hash]);
usedSignatures[hash] = true;
```

Either approach ensures that `revokePusher()` is final: the original signature cannot be replayed to re-establish the delegation.

---

### Proof of Concept

```
1. Creator calls allowPushers(deadline = block.timestamp + 365 days,
                               pushers = [pusher],
                               signatures = [sig])
   → namespaceRemapping[pusher] = creator

2. Pusher's private key is compromised.
   Pusher calls revokePusher()
   → namespaceRemapping[pusher] = address(0)
   → Pusher believes they are safe.

3. Creator (or creator's automation) calls allowPushers(
       deadline = block.timestamp + 365 days,   // same deadline, still valid
       pushers  = [pusher],
       signatures = [sig])                       // SAME signature, no nonce check
   → namespaceRemapping[pusher] = creator        // delegation silently restored

4. Compromised pusher key calls fallback() with crafted slot word:
   [data0:6][data1:6][data2:6][data3:6][ts:7][slotId:1]
   where ts > current stored ts (monotonicity passes),
   price encodes an inverted or extreme U64x32 value.

5. Pool calls getOracleData(feedId) → reads the corrupted slot
   → bad bid/ask price reaches swap execution → trader receives
     more than the oracle curve permits or pool receives less input.
``` [8](#0-7)

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L186-192)
```text
    /// @notice Delegates pusher wallets into the caller's namespace. The pusher's EIP-191
    ///         signature is REQUIRED — without it anyone could remap a foreign pusher
    ///         wallet into their own namespace and silently swallow its pushes. The
    ///         deadline is likewise required: the signed consent carries no timestamp of
    ///         its own, so an undated signature could re-establish a delegation AFTER the
    ///         pusher revoked it.
    function allowPushers(uint256 deadline, address[] calldata pushers, bytes[] memory signatures) external {
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L226-229)
```text
            (bool ok, bytes memory res) = pusher.staticcall(abi.encodeWithSignature("isPusher(address)", msg.sender));
            require(ok);
            bool allowed = abi.decode(res, (bool));
            require(allowed);
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L311-344)
```text
    fallback() override external {
        uint256 end;
        uint256 namespace;

        address creator = namespaceRemapping[msg.sender];
        if (creator == address(0)) creator = msg.sender;

        assembly ("memory-safe") {
            end := calldatasize()
            namespace := shl(96, creator) // [creator:20][zeros:12]
        }

        // 4 * 6 + 7 + 1 = 32 bytes per slot
        if (end == 0 || end % 32 != 0) revert BadCalldataLength();

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
