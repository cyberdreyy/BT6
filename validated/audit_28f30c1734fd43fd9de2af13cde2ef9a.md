Based on my investigation of the codebase, I can identify a valid analog.

---

### Title
Malicious Pusher Front-Runs `removePushers()` to Inject a Persistent Bad Price Into Pool Swaps — (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

---

### Summary

`removePushers()` clears only the `namespaceRemapping` entry for a pusher; it does **not** invalidate the price observations already written into the creator's feed slots. A pusher who observes a pending `removePushers()` call in the mempool can front-run it by pushing a manipulated price with a fresh `timestampMs` into the creator's namespace. After revocation the bad price persists in slot storage and is consumed by every `AnchoredPriceProvider` / `PriceProvider` / `PriceProviderL2` instance bound to that feed, causing pools to execute swaps at the manipulated bid/ask for up to `MAX_REF_STALENESS` seconds (configurable up to 7 days).

---

### Finding Description

**Root cause — revocation is namespace-only, not data-level.**

`removePushers()` does exactly one thing on success:

```solidity
namespaceRemapping[pusher] = address(0);
emit PusherRevoked(pusher, msg.sender);
``` [1](#0-0) 

The underlying slot storage — where the price, spread codebook indexes, and `timestampMs` live — is untouched. `getOracleData(feedId)` will continue to return the last-pushed value indefinitely.

**Push path resolves namespace at push time.**

When a pusher calls the oracle's fallback (the compressed push path), the oracle resolves the effective creator as:

```
creator = namespaceRemapping[msg.sender] != address(0)
              ? namespaceRemapping[msg.sender]   // delegated namespace
              : msg.sender;                       // own namespace
```

The `feedIdOf` is then computed from `(creator, chainid, slotIndex, positionIndex)` and the price is written into that slot. No re-validation of `isPusher` or any other live consent check occurs at push time. [2](#0-1) 

**Attack sequence (EOA pusher path):**

1. Creator registers pusher `P` via `allowPushers(deadline, [P], [sig])`.
2. Creator decides to revoke `P` and broadcasts `removePushers([P])`.
3. `P` observes the pending transaction in the mempool and front-runs it with a push containing `price = manipulated_value`, `timestampMs = block.timestamp * 1000` (fresh, passes monotonicity).
4. `P`'s push lands in the creator's namespace (`namespaceRemapping[P] == creator` at push time) — the bad price is written to the creator's feed slot.
5. Creator's `removePushers` executes: `namespaceRemapping[P] = address(0)`. Slot data is unchanged.
6. `oracle.getOracleData(feedIdOf(creator, slotId, posId)).price == manipulated_value` — persists with a fresh `refTime`.
7. Any `AnchoredPriceProvider` / `PriceProvider` bound to this `feedId` reads the manipulated mid price, computes bid/ask from it, and returns those quotes to the pool.
8. Pool executes swaps at the manipulated rate until the price ages past `MAX_REF_STALENESS`.

**Second vector — `allowContractPushers` stale consent.**

`allowContractPushers` proves consent via a single live `isPusher(creator)` staticcall at registration time:

```solidity
(bool ok, bytes memory res) = pusher.staticcall(
    abi.encodeWithSignature("isPusher(address)", msg.sender)
);
require(ok);
bool allowed = abi.decode(res, (bool));
require(allowed);
namespaceRemapping[pusher] = msg.sender;
``` [3](#0-2) 

After registration, `isPusher` is **never re-checked** on subsequent pushes. If the contract pusher's `isPusher` function is later changed to return `false` (e.g., the pusher contract is upgraded or its internal state changes), the pusher remains in `namespaceRemapping` and can continue pushing prices into the creator's namespace. A creator who believes that changing `isPusher` to `false` constitutes revocation is silently wrong — the oracle ignores it. The pusher can exploit this window to inject bad prices before the creator discovers they must also call `removePushers`.

---

### Impact Explanation

- **Bad-price execution**: Pools call `getBidAndAskPrice()` on the provider, which reads the manipulated mid from the oracle and computes bid/ask from it. Swaps execute at the wrong rate.
- **Duration**: The bad price is valid for up to `MAX_REF_STALENESS` (up to 7 days per the constructor bound). During this window every swap in every pool bound to the feed is mispriced.
- **Magnitude**: Without a `priceGuard`, the pusher can set any price. Even with a guard, the full allowed range (e.g., ±50% of the true price) can be exploited.
- **LP / trader loss**: Traders receive more or fewer tokens than the oracle-correct rate; LPs suffer adverse selection; in extreme cases pool reserves fail to cover LP claims. [4](#0-3) [5](#0-4) 

---

### Likelihood Explanation

- Any authorized pusher who is about to be revoked (key compromise, end of service agreement, detected misbehavior) has a direct financial incentive to front-run the revocation.
- On chains with a public mempool (Ethereum mainnet, Base) the front-run is trivial — a higher-gas copy of the push transaction submitted before `removePushers`.
- The `allowContractPushers` vector requires no mempool visibility: the pusher simply continues pushing after their `isPusher` is changed, exploiting the creator's false belief that the oracle has been updated.

---

### Recommendation

1. **Data-level invalidation on revocation**: When `removePushers()` or `revokePusher()` is called, record a per-feed `revokedAt` timestamp. Price providers should reject any `refTime` that predates `revokedAt` for the revoking creator's feeds.

2. **Re-validate `isPusher` on each push** (contract pushers): In the fallback push path, if `namespaceRemapping[msg.sender] != address(0)`, perform a live `isPusher(creator)` staticcall and revert if it returns `false`. This closes the stale-consent window without requiring the creator to call `removePushers`.

3. **Emit a `FeedInvalidated` event** on revocation so off-chain monitors and price providers can immediately treat the feed as stale.

---

### Proof of Concept

```
State before:
  namespaceRemapping[P] = creator
  oracle.getOracleData(feedIdOf(creator, 2, 3)).price = 100_000  (fair price)

Block N (mempool):
  Tx A (creator): removePushers([P])
  Tx B (P, higher gas): push(slotId=2, pos=3, price=50_000, tsMs=now*1000)

Execution order after front-run:
  1. Tx B executes: slot[creator][2][3] = {price: 50_000, tsMs: now*1000}
  2. Tx A executes: namespaceRemapping[P] = address(0)

Post-state:
  namespaceRemapping[P] = address(0)          ← P is "revoked"
  oracle.getOracleData(feedIdOf(creator,2,3)).price = 50_000  ← bad price persists

Pool swap (any time within MAX_REF_STALENESS):
  provider.getBidAndAskPrice()
    → oracle.price(feedId, pool) → mid = 50_000
    → bid = 50_000 * (1 - halfSpread), ask = 50_000 * (1 + halfSpread)
    → pool executes swap at ~50% of true price
    → trader receives 2× expected tokens; pool is insolvent
``` [1](#0-0) [6](#0-5)

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L163-178)
```text
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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L180-212)
```text
    /*
     *
     * Pusher delegation
     *
     */

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L217-233)
```text
    function allowContractPushers(address[] calldata pushers) external {
        uint256 l = pushers.length;
        for (uint256 i; i < l; i++) {
            address pusher = pushers[i];

            if (pusher == msg.sender) {
                revert NoSelfRemapping();
            }

            (bool ok, bytes memory res) = pusher.staticcall(abi.encodeWithSignature("isPusher(address)", msg.sender));
            require(ok);
            bool allowed = abi.decode(res, (bool));
            require(allowed);

            namespaceRemapping[pusher] = msg.sender;
            emit PusherAuthorized(pusher, msg.sender);
        }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L245-260)
```text
    function removePushers(address[] calldata pushers) external {
        uint256 l = pushers.length;
        for (uint256 i; i < l; i++) {
            address pusher = pushers[i];
            if (pusher == msg.sender) {
                revert NoSelfRemapping();
            }

            if (namespaceRemapping[pusher] == msg.sender) {
                namespaceRemapping[pusher] = address(0);
                emit PusherRevoked(pusher, msg.sender);
            } else {
                revert InvalidManager(msg.sender);
            }
        }
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L151-165)
```text
        MAX_REF_STALENESS = _maxRefStaleness;

        if (_maxSpreadBps == 0 || _maxSpreadBps >= ORACLE_BPS) revert MaxSpreadOutOfBounds();
        MAX_SPREAD_BPS = _maxSpreadBps;

        // minMargin 0 is allowed: the band then relies purely on the oracle spreadBps. If spreadBps is
        // also 0 the band degenerates and the read halts via the refBid >= refAsk guard in _computeBidAsk
        // (never a tighter-than-band quote) — the clamp + that halt are the safety net, not a positive floor.
        // Worst-case half-width must stay below 100% so the clamped bid is always positive.
        if (uint256(_maxSpreadBps) * ONE_BPS_E18 + _minMargin >= BPS_BASE_U) revert BandTooWide();
        minMargin = _minMargin;

        MUTABLE_PARAMS = _mutableParams;
        // marginStep bias + derived step factors (immutable). The customizable variant shapes the quote
        // with confidence then this fixed bias; the load-bearing band clamp in _computeBidAsk keeps the
```

**File:** smart-contracts-poc/contracts/PriceProviderL2.sol (L123-128)
```text
    function getBidAndAskPrice()
        external override returns (uint128 bid, uint128 ask)
    {
        (bid, ask) = _getBidAndAskPrice();
        if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
    }
```
