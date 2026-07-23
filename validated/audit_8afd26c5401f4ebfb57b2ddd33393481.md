### Title
Pusher Revocation Bypassed via Signature Replay in `allowPushers` — (`smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`allowPushers` accepts an EIP-191 signature from the pusher consenting to delegation. After a pusher calls `revokePusher()` to clear their delegation, the creator can immediately replay the original (still-valid, non-expired) signature to re-establish the delegation. There is no nonce, used-signature flag, or per-delegation counter, so the revocation is ineffective for the entire remaining lifetime of the deadline. A pusher who discovers their data is being misused cannot stop their pushes from landing in the creator's namespace until the deadline expires.

---

### Finding Description

`allowPushers` constructs the signed hash as:

```solidity
keccak256(abi.encode(block.chainid, address(this), deadline, pusher, msg.sender))
``` [1](#0-0) 

The only replay guard is `_ensureDeadline(deadline)`, which checks `block.timestamp <= deadline`. [2](#0-1) 

`revokePusher()` clears `namespaceRemapping[msg.sender] = address(0)`: [3](#0-2) 

Because no nonce or used-signature set is maintained, the creator can call `allowPushers(deadline, [pusher], [originalSig])` again with the identical arguments immediately after the pusher's revocation, re-writing `namespaceRemapping[pusher] = creator`. The code comment explicitly acknowledges the risk ("an undated signature could re-establish a delegation AFTER the pusher revoked it") and claims the deadline prevents it — but the deadline only blocks *expired* signatures, not *valid, non-expired* replays. [4](#0-3) 

---

### Impact Explanation

The `CompressedOracle` fallback push path resolves the effective namespace for every push via `namespaceRemapping`: [5](#0-4) 

If a pusher is an automated oracle bot serving multiple creators, and one creator's `PriceProvider` is feeding manipulated or stale prices into a pool, the pusher's only recourse is `revokePusher()`. With the replay bypass, the creator can immediately undo the revocation. The pusher's data continues to land in the creator's namespace, the creator's price provider continues to return those prices via `getBidAndAskPrice()`, and the pool's `swap()` executes at those prices: [6](#0-5) 

The pusher's only effective mitigation is to stop pushing entirely — which harms all other legitimate creators they serve.

---

### Likelihood Explanation

- `allowPushers` is a public, permissionless function callable by any creator.
- The creator holds the original signature (they received it during the initial delegation setup).
- The replay requires no special privilege beyond being the creator who originally called `allowPushers`.
- The window is the full remaining lifetime of the deadline (which the code's own comment suggests should be long enough to be meaningful).

---

### Recommendation

Track consumed delegation signatures with a `mapping(bytes32 => bool) usedDelegations` keyed on the full hash. After a successful `allowPushers` call, mark the hash as used. On `revokePusher()` or `removePushers()`, also mark the hash as consumed so the same signature cannot re-establish the delegation.

Alternatively, include a per-pusher nonce in the signed payload and increment it on every successful delegation or revocation, making each consent single-use.

---

### Proof of Concept

```
1. Pusher signs: hash = keccak256(abi.encode(chainid, oracle, deadline=T+30days, pusher, creator))
2. Creator calls allowPushers(T+30days, [pusher], [sig])
   → namespaceRemapping[pusher] = creator  ✓
3. Pusher discovers creator is misusing their data.
4. Pusher calls revokePusher()
   → namespaceRemapping[pusher] = address(0)  ✓ (revocation appears successful)
5. Creator immediately calls allowPushers(T+30days, [pusher], [sig])  ← same args, same sig
   → _ensureDeadline passes (T+30days > block.timestamp)
   → ECDSA.recover returns pusher  ← same hash, same sig
   → namespaceRemapping[pusher] = creator  ← revocation undone
6. Pusher's subsequent fallback pushes land in creator's namespace.
7. Creator's PriceProvider reads from creator's namespace → getBidAndAskPrice() returns
   pusher-sourced prices → MetricOmmPool.swap() executes at those prices.
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L151-178)
```text
    function _encodeCompressedOracleData(CompressedOracleData memory data) internal pure returns (uint48 raw) {
        raw = (uint48(data.p) << 16) | (uint48(data.s0) << 8) | uint48(data.s1);
    }

    function _decodeCodebookIndex(uint8 index) internal pure returns (uint16 value) {
        bool ok;
        (value, ok) = Codebook256.decode(index);
        if (!ok) revert CodebookDecodeFailed(index);
    }

    /// @notice Unified read path shared with the providers oracle. The compressed oracle is open, so
    ///         `pool` is unused (no in-swap binding) and reads are permissionless.
    function price(bytes32 feedId, address /* pool */)
        external
        view
        returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
    {
        return _price(feedId);
    }

    function _price(bytes32 feedId)
        internal
        view
        returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
    {
        OracleData memory data = getOracleData(feedId);
        return (uint256(data.price), uint256(data.spread0), data.spread1, data.timestampMs.toSeconds());
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L186-212)
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

**File:** metric-core/contracts/MetricOmmPool.sol (L804-810)
```text
  function _getBidAndAskPriceX64() internal returns (uint128 bidPriceX64, uint128 askPriceX64) {
    address activePriceProvider = _resolvedPriceProvider();
    try IPriceProvider(activePriceProvider).getBidAndAskPrice() returns (uint128 bid, uint128 ask) {
      if (bid >= ask) revert BidGreaterThanAsk();
      if (bid == 0) revert BidIsZero();
      return (bid, ask);
    } catch (bytes memory reason) {
```
