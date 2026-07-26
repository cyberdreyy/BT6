### Title
`BridgeCommittee.initializeConfig()` Lacks Access Control, Enabling Front-Running to Permanently Inject Malicious Config — (File: `bridge/evm/contracts/BridgeCommittee.sol`)

---

### Summary

`BridgeCommittee` uses a two-step initialization: `initialize()` sets committee members and stakes, while `initializeConfig()` sets the critical `IBridgeConfig` reference. The second step has **no access control**. Any unprivileged caller can front-run the deployer's `initializeConfig()` call and permanently bind the committee to a malicious config contract, locking all bridge operations that depend on `config` (token transfers, chain validation, limiter initialization) until a UUPS upgrade is executed.

---

### Finding Description

`BridgeCommittee.initialize()` is protected by OpenZeppelin's `initializer` modifier, but `initializeConfig()` is entirely unguarded:

```solidity
function initializeConfig(address _config) external {
    require(address(config) == address(0), "BridgeCommittee: Config already initialized");
    config = IBridgeConfig(_config);
}
``` [1](#0-0) 

The only protection is the "already initialized" check — there is no `onlyOwner`, `onlyAdmin`, or equivalent guard. Once set, `config` cannot be changed through this function.

The deployment script issues `initialize()` and `initializeConfig()` as **separate transactions**:

```solidity
address bridgeCommittee = Upgrades.deployUUPSProxy("BridgeCommittee.sol",
    abi.encodeCall(BridgeCommittee.initialize, (...)), opts);
// ... deploy BridgeConfig in a separate tx ...
BridgeCommittee(bridgeCommittee).initializeConfig(address(bridgeConfig));
``` [2](#0-1) 

Between these two transactions there is an open window. An attacker monitoring the mempool can submit `initializeConfig(address(maliciousConfig))` with higher gas, executing before the deployer's call. The deployer's subsequent call reverts with `"BridgeCommittee: Config already initialized"`.

The `verifySignatures()` function does **not** use `config` — it only reads `committeeStake`, `committeeIndex`, and `blocklist` — so signature verification continues to work normally after the attack. However, every other bridge operation that calls `committee.config()` is now routed through the attacker-controlled contract:

- `SuiBridge.transferBridgedTokensWithSignatures()` calls `committee.config()` to resolve token addresses and validate the target chain. [3](#0-2) 
- `SuiBridge.onlySupportedChain` modifier calls `committee.config().isChainSupported()`. [4](#0-3) 
- `BridgeLimiter.initialize()` calls `committee.config().isChainSupported()` during its own initialization, meaning the limiter cannot be deployed at all if the malicious config reverts or returns `false`. [5](#0-4) 
- `SuiBridge.bridgeERC20()` calls `committee.config()` to validate token support and retrieve the token address. [6](#0-5) 

The analog to the zBanc bug is exact: `BridgeCommittee` is "active" (can verify signatures) immediately after `initialize()`, but its critical configuration variable (`config`) is not yet set and is settable by anyone — mirroring how `DynamicLiquidTokenConverter` became active before its parameters were configured, with setters callable by unauthorized parties.

---

### Impact Explanation

A malicious `IBridgeConfig` can:

1. Return `false` for all `isChainSupported()` calls → `onlySupportedChain` reverts on every deposit and withdrawal → **permanent fund lock** on vault assets until a UUPS upgrade is executed.
2. Return `address(0)` for `tokenAddressOf()` → `_transferTokensFromVault` reverts → no ERC-20 or ETH can be claimed from the vault.
3. Return a wrong `chainID()` → the target-chain check in `transferBridgedTokensWithSignatures` always fails → Sui→EVM unlocks are permanently blocked.
4. Prevent `BridgeLimiter` from being initialized at all, blocking the entire bridge deployment.

The vault holds real user funds (WETH, USDT, WBTC, ETH). Locking them until a committee-quorum UUPS upgrade is performed constitutes **permanent fund lock** under the bounty's High/Medium impact class.

---

### Likelihood Explanation

- The attack requires only mempool monitoring and a single `initializeConfig(maliciousAddress)` call with higher gas — no special privileges, no committee membership, no tokens.
- The deployment script always creates a multi-transaction window between `initialize()` and `initializeConfig()`.
- The function is callable by any EOA or contract.
- The test suite itself demonstrates the pattern: `CommitteeUpgradeableTest` deploys `SuiBridge` with `address(0)` vault and limiter, confirming the two-step setup is the intended (but unguarded) flow. [7](#0-6) 

---

### Recommendation

Combine both initialization steps atomically, or add access control to `initializeConfig()`:

**Option A — Atomic initialization (preferred):**
```solidity
function initialize(
    address[] memory committee,
    uint16[] memory stake,
    uint16 minStakeRequired,
    address _config
) external initializer {
    // ... existing member/stake setup ...
    require(_config != address(0), "BridgeCommittee: config is zero");
    config = IBridgeConfig(_config);
}
```

**Option B — Access control on the separate setter:**
```solidity
function initializeConfig(address _config) external onlyOwner {
    require(address(config) == address(0), "BridgeCommittee: Config already initialized");
    config = IBridgeConfig(_config);
}
```

Either option closes the front-running window. Option A is preferred because it eliminates the partially-configured state entirely, mirroring the zBanc resolution of aligning the upgrade path so the contract is only "active" once fully configured.

---

### Proof of Concept

1. Deployer broadcasts **Tx A**: `BridgeCommittee.initialize(members, stakes, minStake)` — succeeds; `config == address(0)`.
2. Deployer broadcasts **Tx B**: `BridgeConfig.initialize(...)` — succeeds.
3. Deployer broadcasts **Tx C**: `BridgeCommittee.initializeConfig(address(bridgeConfig))` — visible in mempool.
4. Attacker broadcasts **Tx C'** with higher gas: `BridgeCommittee.initializeConfig(address(maliciousConfig))` where `maliciousConfig` is a contract that returns `false` for `isChainSupported()` and `address(0)` for `tokenAddressOf()`.
5. Tx C' executes first; `config = maliciousConfig`.
6. Tx C reverts: `"BridgeCommittee: Config already initialized"`.
7. Deployer attempts `BridgeLimiter.initialize(...)` → calls `committee.config().isChainSupported()` → malicious config returns `false` → `"BridgeLimiter: Chain not supported"` → limiter cannot be deployed.
8. Any attempt to call `SuiBridge.bridgeERC20()` or `transferBridgedTokensWithSignatures()` hits `onlySupportedChain` → malicious config returns `false` → reverts.
9. All vault funds are locked. The only recovery path is a UUPS upgrade requiring committee quorum. [8](#0-7) [5](#0-4) [4](#0-3)

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

**File:** bridge/evm/contracts/SuiBridge.sol (L146-162)
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

**File:** bridge/evm/contracts/BridgeLimiter.sol (L44-51)
```text
        for (uint8 i; i < chainIDs.length; i++) {
            require(
                committee.config().isChainSupported(chainIDs[i]),
                "BridgeLimiter: Chain not supported"
            );
            chainLimits[chainIDs[i]] = _totalLimits[i];
            oldestChainTimestamp[chainIDs[i]] = currentHour();
        }
```

**File:** bridge/evm/test/CommitteeUpgradeableTest.t.sol (L57-63)
```text
        address _bridge = Upgrades.deployUUPSProxy(
            "SuiBridge.sol",
            abi.encodeCall(SuiBridge.initialize, (_committee, address(0), address(0))),
            opts
        );

        bridge = SuiBridge(_bridge);
```
