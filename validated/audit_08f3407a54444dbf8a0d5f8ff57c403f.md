### Title
Provider Deployment via CREATE Enables Reorg-Based Provider Substitution, Feeding Bad Prices into Pools — (`smart-contracts-poc/contracts/AnchoredProviderFactory.sol`, `smart-contracts-poc/contracts/PriceProviderFactory.sol`)

---

### Summary

`AnchoredProviderFactory.createAnchoredProvider()` and `PriceProviderFactory.createPriceProvider()` deploy price providers using the plain `new` keyword (EVM `CREATE` opcode). The deployed address is derived solely from the factory's nonce. On any chain that can experience block reorganizations (Ethereum, Base, HyperEVM — all listed as primary targets), an attacker can exploit a reorg to deploy a malicious provider at the same address a legitimate creator just vacated, causing a pool to consume attacker-controlled prices.

---

### Finding Description

Both factory contracts deploy providers with bare `new`:

```solidity
// AnchoredProviderFactory.sol line 182
AnchoredPriceProvider p = new AnchoredPriceProvider(
    address(this), oracle, baseFeedId, quoteFeedId, ...
);
``` [1](#0-0) 

```solidity
// PriceProviderFactory.sol line 49
PriceProvider p = new PriceProvider(
    address(this), _oracle, _feedId, ...
);
``` [2](#0-1) 

The resulting address is `keccak256(rlp(factory, nonce))`. Neither `msg.sender` nor any caller-supplied salt is mixed in. After a reorg that reverts the factory's nonce, a different caller can deploy a different `AnchoredPriceProvider` (with a different oracle, feedId, or spread parameters) at the identical address.

The factory's `isProvider()` check — the predicate pools rely on to trust a provider — only tests set membership:

```solidity
function isProvider(address provider) external view returns (bool) {
    return _providers.contains(provider);
}
``` [3](#0-2) 

It does not record or verify which caller deployed the provider or with which parameters. A malicious provider that lands at the same address passes `isProvider()` identically to the legitimate one.

---

### Impact Explanation

A pool that was created referencing provider address `X` will call `X.price(feedId, pool)` on every swap. If `X` now belongs to an attacker-controlled `AnchoredPriceProvider` backed by a feedId the attacker pushes to (e.g., a `CompressedOracleV1` slot the attacker owns), the attacker can supply an arbitrary mid-price and spread. This directly causes:

- **Bad-price execution**: swaps execute at attacker-dictated bid/ask, violating swap conservation.
- **LP asset loss**: LPs receive less than the oracle-fair value for their liquidity.
- **Pool insolvency**: if the attacker drains the pool through repeated mispriced swaps, LP claims exceed pool balances.

The `AnchoredPriceProvider` envelope bounds spread and staleness at construction, but the attacker chooses the oracle and feedId freely (subject only to the oracle allow-list). Using a `CompressedOracleV1` feedId the attacker controls, the attacker can push any price within the codebook range to that feedId and have it consumed by the pool.

---

### Likelihood Explanation

- The protocol explicitly targets Ethereum, Base, and HyperEVM — all of which have documented reorg histories.
- The attack requires two conditions: (1) a reorg that reverts the factory's nonce, and (2) the attacker racing to re-deploy before the victim's pool-creation transaction is re-included. Both are feasible on chains with short block times (Base: ~2 s, HyperEVM: sub-second).
- The attacker needs no special privilege: `createAnchoredProvider()` is permissionless.

Overall: **Medium** (low-probability event, high-impact outcome).

---

### Recommendation

Deploy providers via `CREATE2` with a salt that commits to `msg.sender` (and optionally a user-supplied nonce), so the deployed address is caller-specific and cannot be reproduced by a different caller after a reorg:

```solidity
bytes32 salt = keccak256(abi.encode(msg.sender, _providerNonce[msg.sender]++));
AnchoredPriceProvider p = new AnchoredPriceProvider{salt: salt}(...);
```

This mirrors the recommendation in the reference report and is consistent with the protocol's stated use of `CREATE2`/`CREATE3` for pools and routers (noted in the README).

---

### Proof of Concept

1. Factory nonce = N. Alice calls `createAnchoredProvider(oracle=L, feedId=F_alice, ...)` → provider deployed at address `X = CREATE(factory, N)`. Factory nonce advances to N+1. `isProvider(X) == true`, `providerOwner[X] = Alice`.

2. Alice submits a pool-creation transaction referencing provider `X`.

3. A reorg reverts both transactions. Factory nonce returns to N.

4. Bob calls `createAnchoredProvider(oracle=L, feedId=F_bob, ...)` where `F_bob` is a `CompressedOracleV1` feedId Bob controls. Provider deployed at `X = CREATE(factory, N)`. `isProvider(X) == true`, `providerOwner[X] = Bob`.

5. Alice's pool-creation transaction is re-included. The pool is created with provider `X` — now Bob's malicious provider.

6. Bob pushes an extreme price to `F_bob` in `CompressedOracleV1` via the `fallback()` push path. [4](#0-3) 

7. Alice's pool executes swaps at Bob's fabricated price. Bob extracts value from the pool; LPs suffer losses.

### Citations

**File:** smart-contracts-poc/contracts/AnchoredProviderFactory.sol (L182-194)
```text
        AnchoredPriceProvider p = new AnchoredPriceProvider(
            address(this),
            oracle,
            baseFeedId,
            quoteFeedId,
            minMargin,
            maxRefStaleness,
            maxSpreadBps,
            mutableParams,
            marginStep,
            baseToken,
            quoteToken
        );
```

**File:** smart-contracts-poc/contracts/AnchoredProviderFactory.sol (L281-283)
```text
    function isProvider(address provider) external view returns (bool) {
        return _providers.contains(provider);
    }
```

**File:** smart-contracts-poc/contracts/PriceProviderFactory.sol (L49-57)
```text
        PriceProvider p = new PriceProvider(
            address(this),
            _oracle,
            _feedId,
            _marginStep,
            _maxTimeDelta,
            _baseToken,
            _quoteToken
        );
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
