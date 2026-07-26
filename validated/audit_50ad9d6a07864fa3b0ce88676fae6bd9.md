### Title
Unpermissioned `initializeConfig` in `BridgeCommittee` Allows Any Caller to Permanently Inject a Malicious Config, Enabling Illegitimate Token Unlock — (`File: bridge/evm/contracts/BridgeCommittee.sol`)

---

### Summary

`BridgeCommittee.initializeConfig()` carries no caller restriction. Any unprivileged address can race the deployer between the moment `BridgeCommittee` is deployed and the moment the legitimate `BridgeConfig` address is registered. Once a malicious config is injected, the slot is permanently locked (`config != address(0)` blocks all future calls). Every downstream bridge operation — token-transfer decimal conversion, chain-ID gating, token-address resolution — reads from this attacker-controlled contract, enabling illegitimate vault drains on any subsequent valid committee-signed transfer.

---

### Finding Description

The deployment sequence in `deploy_bridge.s.sol` is non-atomic across at least two transactions:

1. Deploy `BridgeCommittee` proxy (committee members set, `config` = `address(0)`).
2. Deploy `BridgeConfig` proxy.
3. Call `BridgeCommittee.initializeConfig(bridgeConfig)` — **separate, unprotected transaction**.

`initializeConfig` in `BridgeCommittee.sol`:

```solidity
function initializeConfig(address _config) external {
    require(address(config) == address(0), "BridgeCommittee: Config already initialized");
    config = IBridgeConfig(_config);
}
```

There is no `onlyOwner`, no `initializer`, no deployer check — just the zero-address guard. Any EOA or contract can call this function in the window between steps 1 and 3 and permanently bind the committee to an attacker-controlled `IBridgeConfig` implementation. [1](#0-0) 

The deployment script confirms the non-atomic gap: [2](#0-1) 

---

### Impact Explanation

`SuiBridge` reads `committee.config()` for every token transfer:

- **`config.tokenAddressOf(tokenID)`** — resolves the ERC-20 address used in `vault.transferERC20()`.
- **`config.tokenSuiDecimalOf(tokenID)`** — used in `BridgeUtils.convertSuiToERC20Decimal()` to scale the signed Sui-side amount into ERC-20 units.
- **`config.chainID()`** — the only chain-ID guard on inbound transfers.
- **`config.isChainSupported()`** — the `onlySupportedChain` modifier. [3](#0-2) [4](#0-3) 

**Concrete drain path:**

A malicious `IBridgeConfig` that returns `tokenSuiDecimalOf(USDC) = 0` (instead of the real `6`) causes `convertSuiToERC20Decimal(erc20Decimals=6, suiDecimals=0, amount)` to multiply the committee-signed amount by `10^6` before passing it to `vault.transferERC20`. A legitimate committee-signed message for 1 USDC (Sui amount = `1_000_000`) would unlock `1_000_000 × 10^6 = 10^12` USDC units from the vault — a complete drain — using only a valid, honestly-signed bridge message.

Similarly, a malicious `tokenAddressOf` can redirect vault withdrawals to an attacker-controlled ERC-20 contract, and a malicious `isChainSupported` returning `true` for all chain IDs removes the chain-ID domain separation entirely.

---

### Likelihood Explanation

- The attack requires only a single unpermissioned `external` call with no ETH value and no special capability.
- The window is open from the moment the `BridgeCommittee` proxy is live on-chain until `initializeConfig` is called — typically at least one block, often more during multi-step deployment scripts.
- Front-running is trivially achievable by any mempool observer with a higher gas price.
- The effect is permanent and irreversible: once `config != address(0)`, the slot cannot be updated.

---

### Recommendation

Add an access-control guard to `initializeConfig`. The simplest fix consistent with the existing pattern is to restrict it to the deployer address captured during `initialize`, or to merge the config address into the existing `initialize` call so the entire setup is atomic:

```solidity
// Option A: pass config in the same initializer call
function initialize(
    address[] memory committee,
    uint16[] memory stake,
    uint16 minStakeRequired,
    address _config          // ← add this
) external initializer { ... }

// Option B: restrict initializeConfig to the deployer
address private _deployer;
function initialize(...) external initializer {
    _deployer = msg.sender;
    ...
}
function initializeConfig(address _config) external {
    require(msg.sender == _deployer, "BridgeCommittee: not deployer");
    require(address(config) == address(0), "BridgeCommittee: Config already initialized");
    config = IBridgeConfig(_config);
}
```

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// Attacker deploys this malicious config
contract MaliciousConfig {
    // Returns 0 Sui decimals for every token → amount × 10^erc20Decimals drain
    function tokenSuiDecimalOf(uint8) external pure returns (uint8) { return 0; }
    // Returns real USDC address so vault.transferERC20 succeeds
    function tokenAddressOf(uint8) external pure returns (address) {
        return 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48; // mainnet USDC
    }
    function chainID() external pure returns (uint8) { return 11; } // Ethereum mainnet
    function isTokenSupported(uint8) external pure returns (bool) { return true; }
    function isChainSupported(uint8) external pure returns (bool) { return true; }
}

// Attack sequence (pseudocode):
// 1. Deployer broadcasts tx to deploy BridgeCommittee proxy (tx A, pending in mempool)
// 2. Attacker sees tx A, deploys MaliciousConfig (tx B)
// 3. After tx A mines, attacker calls:
//      BridgeCommittee(committeeProxy).initializeConfig(address(maliciousConfig));
//    with higher gas than the deployer's pending initializeConfig tx.
// 4. Deployer's initializeConfig reverts: "Config already initialized"
// 5. Any subsequent valid committee-signed TOKEN_TRANSFER message now uses
//    MaliciousConfig.tokenSuiDecimalOf() = 0, multiplying every withdrawal
//    amount by 10^(erc20Decimals), draining the vault.
``` [5](#0-4) [6](#0-5)

### Citations

**File:** bridge/evm/contracts/BridgeCommittee.sol (L59-66)
```text
    /// @notice Initializes the contract with the provided parameters.
    /// @dev This function should be called directly after config deployment. The config contract address
    /// provided should be verified before bridging any assets.
    /// @param _config The address of the BridgeConfig contract.
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

**File:** bridge/evm/contracts/SuiBridge.sol (L244-265)
```text
    function _transferTokensFromVault(
        uint8 sendingChainID,
        uint8 tokenID,
        address recipientAddress,
        uint256 amount
    ) private whenNotPaused limitNotExceeded(sendingChainID, tokenID, amount) {
        address tokenAddress = committee.config().tokenAddressOf(tokenID);

        // Check that the token address is supported
        require(tokenAddress != address(0), "SuiBridge: Unsupported token");

        // transfer eth if token type is eth
        if (tokenID == BridgeUtils.ETH) {
            vault.transferETH(payable(recipientAddress), amount);
        } else {
            // transfer tokens from vault to target address
            vault.transferERC20(tokenAddress, recipientAddress, amount);
        }

        // update amount bridged
        limiter.recordBridgeTransfers(sendingChainID, tokenID, amount);
    }
```
