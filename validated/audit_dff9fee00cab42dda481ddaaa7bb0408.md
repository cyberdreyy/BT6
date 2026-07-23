### Title
`stateGuard` role has no ADMIN override — inaccessible guard permanently bricks feed parameter control and can freeze pool swaps — (File: `smart-contracts-poc/contracts/oracles/providers/OracleBase.sol`)

---

### Summary

Once `stateGuard[feedId]` is set to a non-zero address, every `checkRole`-gated function — including `purgeStateGuardRole` itself — requires `msg.sender == stateGuard[feedId]`. There is no ADMIN escape hatch. If the guard address becomes inaccessible (key loss, contract self-destruct, multisig quorum loss), the feed's price guard and guard-role management are permanently frozen with no recovery path, directly analogous to the ArtGobblers M-02 pattern where `upgradeRandProvider` was blocked by `waitingForSeed`.

---

### Finding Description

The `checkRole` modifier in `OracleBase.sol` grants exclusive control to `stateGuard[feedId]` once it is set:

```solidity
// OracleBase.sol lines 65-74
modifier checkRole(bytes32 feedId) {
    address _guard = stateGuard[feedId];
    if (_guard != address(0)) {
        require(_guard == msg.sender, InvalidGuard(msg.sender));
    } else {
        _checkRole(ADMIN_ROLE);
    }
    _;
}
``` [1](#0-0) 

All four feed-management functions are gated exclusively by this modifier:

| Function | Effect |
|---|---|
| `setPriceGuard` | Update min/max price bounds |
| `setStateGuardRole` | Nominate a new pending guard |
| `purgePendingStateGuardRole` | Cancel a pending nomination |
| `purgeStateGuardRole` | **The only recovery path — remove the guard** | [2](#0-1) 

The critical invariant break: `purgeStateGuardRole` — the sole mechanism to remove a stuck guard — is itself gated by `checkRole`:

```solidity
// OracleBase.sol lines 120-124
function purgeStateGuardRole(bytes32 feedId) external checkRole(feedId) {
    delete stateGuard[feedId];
    emit StateGuardDeleted(feedId);
}
``` [3](#0-2) 

Likewise, `setStateGuardRole` (the path to nominate a replacement) is also gated:

```solidity
// OracleBase.sol lines 99-103
function setStateGuardRole(bytes32 feedId, address newGuard) external checkRole(feedId) {
    pendingStateGuard[feedId] = newGuard;
    emit StateGuardPending(feedId, newGuard);
}
``` [4](#0-3) 

There is no ADMIN override, no timelock bypass, and no emergency path. Once the guard is inaccessible, the state is permanently stuck — identical to the M-02 pattern where `waitingForSeed = true` blocked both `revealGobblers` and `upgradeRandProvider`.

---

### Impact Explanation

The downstream consequence flows directly through `PriceProvider._getBidAndAskPrice()`:

```solidity
// PriceProvider.sol lines 207-212
(uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(offchainFeedId);
guardMax = guardMax == 0 ? type(uint128).max : guardMax;
if (mid < guardMin || mid > guardMax) {
    return (0, type(uint128).max);
}
``` [5](#0-4) 

A frozen price guard with tight bounds causes `_getBidAndAskPrice` to return `(0, type(uint128).max)` whenever the market price drifts outside those bounds. `getBidAndAskPrice()` then reverts with `FeedStalled`:

```solidity
// PriceProvider.sol lines 118-120
function getBidAndAskPrice() external override returns (uint128 bid, uint128 ask) {
    (bid, ask) = _getBidAndAskPrice();
    if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
}
``` [6](#0-5) 

Every swap on every pool using this feed is permanently blocked. The same path applies to `AnchoredPriceProvider._readLeg`, which also reads `priceGuard` and halts on violation. [7](#0-6) 

**Impact class:** Broken core pool functionality — unusable swap/liquidity flows. Matches the allowed impact gate.

---

### Likelihood Explanation

The `stateGuard` is set via a two-step process: ADMIN nominates via `setStateGuardRole`, and the guard accepts via `acceptStateGuardRole`. The guard becoming inaccessible is the same class of externality as M-02 (a `randProvider` being deprecated or retired): key loss, a contract guard being upgraded or self-destructed, or a multisig losing quorum. Any production deployment that uses a contract address (e.g., a DAO, a multisig, or an automated keeper) as the guard is exposed. The risk is non-trivial and requires no attacker — only an operational failure of the guard.

---

### Recommendation

Add an ADMIN override to `purgeStateGuardRole` (and symmetrically to `setStateGuardRole`) that bypasses the guard check, mirroring the ArtGobblers M-02 fix:

```solidity
function purgeStateGuardRole(bytes32 feedId) external {
    address _guard = stateGuard[feedId];
    if (_guard != address(0) && _guard != msg.sender) {
        // ADMIN can always remove a stuck/inaccessible guard
        _checkRole(ADMIN_ROLE);
    }
    delete stateGuard[feedId];
    emit StateGuardDeleted(feedId);
}
```

This preserves the guard's exclusive control under normal operation while giving ADMIN a recovery path when the guard becomes inaccessible — exactly the fix applied in ArtGobblers PR #154.

---

### Proof of Concept

```
1. ADMIN calls setStateGuardRole(feedId, guardAddress)
   → pendingStateGuard[feedId] = guardAddress

2. guardAddress calls acceptStateGuardRole(feedId)
   → stateGuard[feedId] = guardAddress

3. guardAddress calls setPriceGuard(feedId, 100_000_000, 100_000_001)
   (tight bounds anchored at current market price)

4. guardAddress becomes inaccessible (key lost / contract bricked)

5. Market price moves to 200_000_000 (outside guard range)

6. ADMIN calls purgeStateGuardRole(feedId)
   → checkRole: stateGuard[feedId] != address(0) && msg.sender != guardAddress
   → REVERT: InvalidGuard(ADMIN)

7. ADMIN calls setPriceGuard(feedId, 0, 0)
   → REVERT: InvalidGuard(ADMIN)

8. Pool calls getBidAndAskPrice()
   → _getBidAndAskPrice: mid=200_000_000 > guardMax=100_000_001
   → returns (0, type(uint128).max)
   → REVERT: FeedStalled()

9. All swaps permanently blocked. No recovery path exists.
```

### Citations

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L65-74)
```text
    modifier checkRole(bytes32 feedId) {
        address _guard = stateGuard[feedId];
        if (_guard != address(0)) {
            require(_guard == msg.sender, InvalidGuard(msg.sender));
        } else {
            _checkRole(ADMIN_ROLE);
        }

        _;
    }
```

**File:** smart-contracts-poc/contracts/oracles/providers/OracleBase.sol (L88-124)
```text
    function setPriceGuard(bytes32 feedId, uint128 minPrice, uint128 maxPrice)
        external
        checkRole(feedId)
    {
        require(minPrice < maxPrice);

        priceGuard[feedId] = PriceGuard({min: minPrice, max: maxPrice});

        emit PriceGuardUpdated(feedId, minPrice, maxPrice);
    }

    function setStateGuardRole(bytes32 feedId, address newGuard) external checkRole(feedId) {
        pendingStateGuard[feedId] = newGuard;

        emit StateGuardPending(feedId, newGuard);
    }

    function purgePendingStateGuardRole(bytes32 feedId) external checkRole(feedId) {
        delete pendingStateGuard[feedId];

        emit PendingStateGuardDeleted(feedId);
    }

    function acceptStateGuardRole(bytes32 feedId) external {
        require(pendingStateGuard[feedId] == msg.sender, InvalidGuard(msg.sender));

        delete pendingStateGuard[feedId];
        stateGuard[feedId] = msg.sender;

        emit StateGuardUpdated(feedId, msg.sender);
    }

    function purgeStateGuardRole(bytes32 feedId) external checkRole(feedId) {
        delete stateGuard[feedId];

        emit StateGuardDeleted(feedId);
    }
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L118-120)
```text
        (bid, ask) = _getBidAndAskPrice();
        if (bid == 0 || ask == type(uint128).max) revert FeedStalled();
    }
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L207-212)
```text
        // 4. Price guard check (moved from oracle)
        (uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(offchainFeedId);
        guardMax = guardMax == 0 ? type(uint128).max : guardMax;
        if (mid < guardMin || mid > guardMax) {
            return (0, type(uint128).max);
        }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L289-293)
```text
        // Per-leg price guard.
        (uint128 guardMin, uint128 guardMax) = offchainOracle.priceGuard(feedId);
        guardMax = guardMax == 0 ? type(uint128).max : guardMax;
        if (mid < guardMin || mid > guardMax) return (mid, spreadBps, refTime, false);

```
