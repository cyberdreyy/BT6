### Title
Missing Zero-Address Validation for `_oracle` in Provider Constructors Allows Permanently Broken Price Provider to Be Deployed and Wired to a Pool — (`smart-contracts-poc/contracts/PriceProvider.sol`, `PriceProviderL2.sol`, `ProtectedPriceProvider.sol`, `ProtectedPriceProviderL2.sol`, `AnchoredPriceProvider.sol`)

---

### Summary

Every price-provider constructor accepts `_oracle` and casts it directly to `IOffchainOracle` without a zero-address guard. The permissionless `PriceProviderFactory.createPriceProvider()` forwards the caller-supplied oracle address unchanged. A provider deployed with `_oracle = address(0)` is permanently broken: every `getBidAndAskPrice()` call reverts because the ABI decoder fails on the empty returndata from a call to `address(0)`. Any pool wired to such a provider has its swap path permanently disabled.

---

### Finding Description

In all five provider constructors the oracle is stored without validation:

```solidity
// PriceProvider.sol line 73 (identical pattern in all variants)
offchainOracle = IOffchainOracle(_oracle);   // no require(_oracle != address(0))
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

The permissionless factory passes the caller-supplied address straight through with no oracle allow-list or zero check:

```solidity
// PriceProviderFactory.sol – no oracle validation before deployment
PriceProvider p = new PriceProvider(
    address(this), _oracle, _feedId, ...
);
``` [6](#0-5) 

By contrast, `AnchoredProviderFactory` explicitly guards against this:

```solidity
require(oracle != address(0), ZeroOracle());
``` [7](#0-6) 

When `getBidAndAskPrice()` is called on a null-oracle provider, the internal read path executes:

```solidity
(uint256 mid, uint256 spread, , uint256 refTime) =
    IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender);
``` [8](#0-7) 

A call to `address(0)` in the EVM succeeds but returns empty bytes. Solidity's ABI decoder then reverts attempting to decode `(uint256, uint256, uint16, uint256)` from zero-length returndata. The revert propagates through `getBidAndAskPrice()` into the pool's swap path.

---

### Impact Explanation

Any pool whose admin sets a null-oracle provider has its swap flow permanently broken. `getBidAndAskPrice()` reverts on every call; the pool cannot execute any swap. LPs retain withdrawal access (remove-liquidity does not call the price provider), but the pool's primary function — swap execution — is permanently disabled. This satisfies the "unusable swap/liquidity flows" impact criterion.

---

### Likelihood Explanation

`PriceProviderFactory.createPriceProvider()` is permissionless. Any EOA can deploy a null-oracle provider at zero cost; the factory registers it as a valid provider. A pool admin (semi-trusted) who selects this provider — whether through error, UI confusion, or social engineering — permanently bricks the pool's swap path. The asymmetry with `AnchoredProviderFactory` (which does enforce `oracle != address(0)`) shows the guard was known to be necessary but was omitted from the simpler factory.

---

### Recommendation

Add a zero-address guard for `_oracle` in every provider constructor, mirroring the existing `_factory` and token checks:

```solidity
require(_oracle != address(0));
offchainOracle = IOffchainOracle(_oracle);
```

Additionally, add the same guard to `PriceProviderFactory.createPriceProvider()` before forwarding to the constructor, consistent with `AnchoredProviderFactory.addOracle()`.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {PriceProviderFactory} from "smart-contracts-poc/contracts/PriceProviderFactory.sol";
import {PriceProvider} from "smart-contracts-poc/contracts/PriceProvider.sol";

contract PoC {
    function run(address factory) external {
        // Permissionless: anyone can create a null-oracle provider
        address provider = PriceProviderFactory(factory).createPriceProvider(
            address(0),          // _oracle = zero address — no revert
            bytes32("some-feed"),
            0,                   // marginStep
            1 days,              // maxTimeDelta
            address(0xBEEF),     // baseToken
            address(0xCAFE)      // quoteToken
        );

        // Provider is now tracked as valid by the factory.
        // Pool admin sets it. Every subsequent swap call reverts:
        PriceProvider(provider).getBidAndAskPrice(); // ← reverts (ABI decode on empty returndata)
    }
}
```

### Citations

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L70-74)
```text
        require(_factory != address(0));
        factory = _factory;

        offchainOracle = IOffchainOracle(_oracle);
        offchainFeedId = _offchainFeedId;
```

**File:** smart-contracts-poc/contracts/PriceProvider.sol (L191-196)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        // 1. Read via the unified price(feedId, pool) path, forwarding the pool (msg.sender).
        //    refTime is already in seconds.
        (uint256 mid, uint256 spread, , uint256 refTime) =
            IPricedOracle(address(offchainOracle)).price(offchainFeedId, msg.sender);

```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L136-141)
```text
        require(_factory != address(0));
        factory = _factory;

        offchainOracle = IOffchainOracle(_oracle);
        baseFeedId = _baseFeedId;
        quoteFeedId = _quoteFeedId;
```

**File:** smart-contracts-poc/contracts/ProtectedPriceProvider.sol (L72-76)
```text
        require(_factory != address(0));
        factory = _factory;

        offchainOracle = IOffchainOracle(_oracle);
        offchainFeedId = _offchainFeedId;
```

**File:** smart-contracts-poc/contracts/PriceProviderL2.sol (L74-78)
```text
        require(_factory != address(0));
        factory = _factory;

        offchainOracle = IOffchainOracle(_oracle);
        offchainFeedId = _offchainFeedId;
```

**File:** smart-contracts-poc/contracts/ProtectedPriceProviderL2.sol (L78-82)
```text
        require(_factory != address(0));
        factory = _factory;

        offchainOracle = IOffchainOracle(_oracle);
        offchainFeedId = _offchainFeedId;
```

**File:** smart-contracts-poc/contracts/PriceProviderFactory.sol (L41-57)
```text
    function createPriceProvider(
        address _oracle,
        bytes32 _feedId,
        int256  _marginStep,
        uint256 _maxTimeDelta,
        address _baseToken,
        address _quoteToken
    ) external override returns (address provider) {
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

**File:** smart-contracts-poc/contracts/AnchoredProviderFactory.sol (L70-73)
```text
    function addOracle(address oracle) external override onlyRole(ADMIN_ROLE) {
        require(oracle != address(0), ZeroOracle());
        require(_oracles.add(oracle), OracleAlreadyAllowed(oracle));
        emit OracleAdded(oracle);
```
