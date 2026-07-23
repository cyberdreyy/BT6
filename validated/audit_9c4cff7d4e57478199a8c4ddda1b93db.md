### Title
`priceGuard` bounds are set but never enforced on push or read paths in `CompressedOracleV1` — bad prices bypass the guard and reach pool swaps - (File: `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol`)

### Summary

`CompressedOracleV1` inherits `setPriceGuard` from its `OracleBase`, allowing a creator or `stateGuard` to configure per-feed min/max price bounds. However, neither the write paths (`fallback()`, `updateBySignature()`) nor the read path (`price()` / `getOracleData()`) ever consult `priceGuard`. An authorized pusher can write any price — including one far outside the configured bounds — and that price is stored and served to any consumer, including pools executing live swaps.

### Finding Description

`OracleBase` (compressed) exposes `setPriceGuard`, gated by `checkRole` (creator or explicit `stateGuard`), which stores a `PriceGuard{min, max}` struct per feed: [1](#0-0) 

The intent is clear: the creator can bound the acceptable price range for their feed. However, the two push paths in `CompressedOracleV1` apply only a timestamp-monotonicity check and a future-drift check — no `priceGuard` lookup: [2](#0-1) [3](#0-2) 

The read path is equally unguarded: [4](#0-3) [5](#0-4) 

The `priceGuard` mapping is populated but consulted nowhere in the contract. This is the direct analog of the SimpleShares bug: a protective state (`priceGuard` ≈ "paused") is configured by the authority, but the operational paths (`fallback`/`updateBySignature` ≈ `distribute`) execute without checking it.

### Impact Explanation

`CompressedOracleV1` implements `IOffchainOracle` and can be wired as the `offchainOracle` of an `AnchoredPriceProvider` or `PriceProvider`, which pools call during `swap()`: [6](#0-5) [7](#0-6) 

If a pusher writes a price outside the creator's configured `priceGuard` bounds, the `AnchoredPriceProvider` receives that unclamped mid, computes bid/ask from it, and the pool executes the swap at the corrupted price. This satisfies the **bad-price execution** impact gate: an unbounded bid/ask quote reaches a pool swap, causing traders to receive more than the oracle/bin curve permits or LPs to suffer direct principal loss.

### Likelihood Explanation

The trigger is a **semi-trusted authorized pusher** — an address in `namespaceRemapping[pusher] == creator` (granted via `allowPushers` with a valid EIP-191 signature, or `allowContractPushers`). A compromised pusher key, a buggy off-chain pusher bot, or a malicious contract pusher whose `isPusher()` was approved can all push an out-of-bounds price. The creator set `priceGuard` precisely to defend against this scenario; the missing enforcement makes that defense illusory. [8](#0-7) [9](#0-8) 

### Recommendation

**Short term:** Add a `priceGuard` check inside both `fallback()` and `updateBySignature()` immediately after decoding the price from the slot word. Reject (or skip) any word whose decoded price falls outside `[priceGuard[feedId].min, priceGuard[feedId].max]` when the guard is set (i.e., `min != 0 || max != 0`).

**Long term:** Also enforce the guard in `getOracleData()` / `_price()` so that a stale guard-violating value already in storage cannot be served to consumers even if it was written before the guard was configured.

### Proof of Concept

```solidity
// 1. Creator sets a priceGuard: ETH/USD must stay between 1_000 and 10_000 (8-dec)
bytes32 feedId = oracle.feedIdOf(creator, 0, 0);
vm.prank(creator);
oracle.setPriceGuard(feedId, 1_000e8, 10_000e8);

// 2. Authorized pusher pushes price = $1 (far below min)
uint56 tsMs = uint56(block.timestamp * 1000);
uint48 raw = _packRaw(/* U64x32-encoded $1 */ 1_000_000, 5, 5);
vm.prank(pusher); // pusher is in namespaceRemapping[pusher] == creator
(bool ok,) = address(oracle).call(_wordAt(0, 0, raw, tsMs));
assertTrue(ok); // succeeds — priceGuard is never checked

// 3. Pool reads the corrupted price through AnchoredPriceProvider
// oracle.price(feedId, pool) returns mid = $1
// AnchoredPriceProvider computes bid/ask from $1 and passes it to the pool
// Pool executes swap at $1 instead of ~$3000 → massive LP loss
IOffchainOracle.OracleData memory data = oracle.getOracleData(feedId);
assertEq(data.price, U64x32.decode(uint32(raw >> 16))); // $1 stored, guard ignored
``` [10](#0-9)

### Citations

**File:** smart-contracts-poc/contracts/oracles/compressed/OracleBase.sol (L17-57)
```text
    mapping(bytes32 => PriceGuard) public priceGuard;
    mapping(bytes32 => address) public pendingStateGuard;
    mapping(bytes32 => address) public stateGuard;

    uint16 public constant BPS_BASE = 10_000;

    constructor(address _owner, uint256 maxTimeDrift) {
        _grantRole(ADMIN_ROLE, _owner);
        _setRoleAdmin(ADMIN_ROLE, ADMIN_ROLE);
        MAX_TIME_DRIFT = maxTimeDrift;
    }

    /// Feeds are registrationless: guard authority is the explicit stateGuard when set,
    /// else the feed's default authority resolved from the feedId itself (see _defaultGuard).
    modifier checkRole(bytes32 feedId) {
        address guard = stateGuard[feedId];
        if (guard == address(0)) guard = _defaultGuard(feedId);
        require(guard == msg.sender, InvalidGuard(msg.sender));
        _;
    }

    /// The authority a feed falls back to before an explicit stateGuard is accepted.
    function _defaultGuard(bytes32) internal view virtual returns (address) {
        return address(0);
    }

    /*
     *
     * Service functions
     *
     */

    function setPriceGuard(bytes32 feedId, uint128 minPrice, uint128 maxPrice)
        external
        checkRole(feedId)
    {
        require(minPrice < maxPrice);

        priceGuard[feedId] = PriceGuard({min: minPrice, max: maxPrice});

        emit PriceGuardUpdated(feedId, minPrice, maxPrice);
```

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

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L217-234)
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
    }
```

**File:** smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol (L283-302)
```text
        uint8 slotId = uint8(newSlotValue); // LSB
        TimeMs timestampMs = toTimeMs(newSlotValue >> 8 & X56);
        timestampMs.revertIfAfterBlockTimeWithDrift(MAX_TIME_DRIFT);
        bytes32 key = bytes32(namespace | uint256(slotId));
        uint256 old = uint256(_loadStorage(key));
        TimeMs oldTimestampMs = toTimeMs(old >> 8 & X56);

        bool newer = timestampMs.isAfter(oldTimestampMs);
        if (!newer) {
            return false;
        }

        bytes32 hash = MessageHashUtils.toEthSignedMessageHash(
            keccak256(abi.encode(block.chainid, address(this), feedCreator, newSlotValue))
        );
        require(feedCreator == ECDSA.recover(hash, signature));

        _writeStorage(key, bytes32(newSlotValue & ~uint256(0xff)));

        return true;
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

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L65-66)
```text
    IOffchainOracle public immutable offchainOracle;
    bytes32         public immutable baseFeedId;
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L258-270)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        (uint256 mid, uint256 spreadBps, , bool ok) = _readLeg(baseFeedId);
        if (!ok) return (0, type(uint128).max);

        bytes32 _quote = quoteFeedId;
        if (_quote != bytes32(0)) {
            (uint256 mid2, uint256 spreadBps2, , bool ok2) = _readLeg(_quote);
            if (!ok2 || mid2 == 0) return (0, type(uint128).max);
            // Synthetic ratio (8-decimal): mid1 / mid2. Relative uncertainties of a ratio add.
            mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);
            spreadBps += spreadBps2;
        }

```
