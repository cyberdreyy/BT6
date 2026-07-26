### Title
Unguarded `initializeConfig` on `BridgeCommittee` Allows Front-Running to Inject Malicious Config, Permanently Bricking the Bridge — (`File: bridge/evm/contracts/BridgeCommittee.sol`)

---

### Summary

`BridgeCommittee.initializeConfig` is a permissionless `external` function with no caller restriction. The deployment script calls it in a separate transaction after deploying the proxy, creating a front-running window identical in structure to the NEAR nft-bridge race condition. An attacker who wins that window sets `config` to an attacker-controlled contract, permanently poisoning every downstream bridge operation — including the committee-signed upgrade path — with no on-chain recovery route.

---

### Finding Description

`BridgeCommittee.sol` exposes:

```solidity
// bridge/evm/contracts/BridgeCommittee.sol  lines 63-66
function initializeConfig(address _config) external {
    require(address(config) == address(0), "BridgeCommittee: Config already initialized");
    config = IBridgeConfig(_config);
}
``` [1](#0-0) 

There is no `onlyOwner`, no `initializer`, and no deployer check. The only guard is the zero-address sentinel, which is satisfied before the deployer's own call arrives.

The deployment script issues three separate on-chain transactions:

1. Deploy `BridgeCommittee` proxy (lines 124–135)
2. Deploy `BridgeConfig` proxy (lines 157–172)
3. Call `committee.initializeConfig(address(bridgeConfig))` (line 175) [2](#0-1) 

Between transactions 1 and 3 there is an open mempool window. Any address can call `initializeConfig(attackerControlledConfig)` and win the race. Once set, the field cannot be overwritten — the `require(address(config) == address(0))` guard permanently locks in whatever value was written first.

The script also directly initializes the implementation contract at line 178:

```solidity
committeeImplementation.initializeConfig(address(bridgeConfig));
``` [3](#0-2) 

That call is equally front-runnable.

---

### Impact Explanation

`committee.config()` is the single source of truth consumed by every security-critical path in the bridge:

**`MessageVerifier.verifyMessageAndSignatures`** (used by every governance and upgrade operation):

```solidity
// bridge/evm/contracts/utils/MessageVerifier.sol  lines 46-48
require(
    message.chainID == committee.config().chainID(), "MessageVerifier: Invalid chain ID"
);
``` [4](#0-3) 

If the attacker's config returns a `chainID()` that never matches any real message, every governance call — including `upgradeWithSignatures` on `SuiBridge`, `BridgeConfig`, `BridgeLimiter`, and `BridgeCommittee` itself — reverts permanently. There is no alternative upgrade path.

**`SuiBridge.onlySupportedChain`** (guards `bridgeERC20` and `bridgeETH`):

```solidity
// bridge/evm/contracts/SuiBridge.sol  lines 283-288
modifier onlySupportedChain(uint8 targetChainID) {
    require(
        committee.config().isChainSupported(targetChainID),
        "SuiBridge: Target chain not supported"
    );
``` [5](#0-4) 

A malicious config returning `false` for all chains blocks all deposits.

**`SuiBridge.transferBridgedTokensWithSignatures`** and **`_transferTokensFromVault`**:

```solidity
// bridge/evm/contracts/SuiBridge.sol  lines 67, 74, 79, 250
IBridgeConfig config = committee.config();
require(tokenTransferPayload.targetChain == config.chainID(), ...);
IERC20Metadata(config.tokenAddressOf(tokenTransferPayload.tokenID)).decimals()
address tokenAddress = committee.config().tokenAddressOf(tokenID);
``` [6](#0-5) [7](#0-6) 

A malicious config can redirect `tokenAddressOf` to return an arbitrary ERC-20 address, causing the vault to attempt transfers of the wrong token — either reverting (DoS) or, if the vault holds multiple assets, transferring the wrong asset to the recipient (fund misrouting).

**Combined effect:** The attacker permanently bricks the bridge and locks all vault funds with a single front-run transaction costing only gas. Because the upgrade path itself goes through `verifyMessageAndSignatures → committee.config().chainID()`, even a committee-signed upgrade cannot recover the system.

---

### Likelihood Explanation

- Ethereum mainnet has active MEV infrastructure (Flashbots, private mempools) that routinely front-runs deployment sequences.
- The attack requires zero capital and zero privilege — any EOA can call `initializeConfig`.
- The deployment script issues the three transactions sequentially with no atomicity guarantee; the window is at least one block wide.
- The attacker needs only to monitor the mempool for the `BridgeCommittee` proxy deployment and immediately submit `initializeConfig(maliciousConfig)` with a higher gas price.

---

### Recommendation

Merge the config initialization into the `initialize` function so that deployment and config binding are atomic within a single transaction:

```solidity
function initialize(
    address[] memory committee,
    uint16[] memory stake,
    uint16 minStakeRequired,
    address _config          // add this
) external initializer {
    __CommitteeUpgradeable_init(address(this));
    __UUPSUpgradeable_init();
    // ... existing stake setup ...
    config = IBridgeConfig(_config);   // set atomically
}
```

Remove `initializeConfig` entirely, or — if a two-step flow is unavoidable — restrict it to `msg.sender == deployer` captured during `initialize` and enforce it can only be called once within the same deployment transaction bundle.

---

### Proof of Concept

```
Block N:   Deployer broadcasts Tx-A: deployUUPSProxy("BridgeCommittee.sol", initialize(...))
           → BridgeCommittee proxy lands at address C

Block N:   Attacker sees Tx-A in mempool, deploys MaliciousConfig contract M
           (M.chainID() returns 0xFF, M.isChainSupported() returns false for all chains,
            M.tokenAddressOf() returns address(0))

Block N+1: Attacker broadcasts Tx-B: BridgeCommittee(C).initializeConfig(M)
           with gas price > deployer's Tx-C

Block N+1: Tx-B lands first. BridgeCommittee.config = M. Guard now reads config != address(0).

Block N+1: Deployer's Tx-C: BridgeCommittee(C).initializeConfig(realConfig)
           → REVERTS: "BridgeCommittee: Config already initialized"

Result:
  - committee.config() == M permanently
  - SuiBridge.bridgeERC20 / bridgeETH → revert (onlySupportedChain: M.isChainSupported = false)
  - SuiBridge.transferBridgedTokensWithSignatures → revert (chainID mismatch)
  - SuiBridge.upgradeWithSignatures → revert (MessageVerifier chainID check against M.chainID())
  - All vault funds permanently locked; no on-chain recovery path exists
```

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

**File:** bridge/evm/contracts/utils/MessageVerifier.sol (L44-49)
```text
            // verify chain ID
            require(
                message.chainID == committee.config().chainID(), "MessageVerifier: Invalid chain ID"
            );
            require(message.nonce == nonces[message.messageType], "MessageVerifier: Invalid nonce");
            nonces[message.messageType]++;
```

**File:** bridge/evm/contracts/SuiBridge.sol (L67-82)
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
```

**File:** bridge/evm/contracts/SuiBridge.sol (L249-251)
```text
    ) private whenNotPaused limitNotExceeded(sendingChainID, tokenID, amount) {
        address tokenAddress = committee.config().tokenAddressOf(tokenID);

```

**File:** bridge/evm/contracts/SuiBridge.sol (L283-288)
```text
    modifier onlySupportedChain(uint8 targetChainID) {
        require(
            committee.config().isChainSupported(targetChainID),
            "SuiBridge: Target chain not supported"
        );
        _;
```
