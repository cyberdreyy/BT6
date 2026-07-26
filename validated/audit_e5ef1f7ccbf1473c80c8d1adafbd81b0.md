### Title
Unprotected `BridgeCommittee.initializeConfig()` Allows Frontrun to Inject Malicious Bridge Config — (`File: bridge/evm/contracts/BridgeCommittee.sol`)

---

### Summary

`BridgeCommittee.initializeConfig()` has no access control and is called as a **separate transaction** from the committee deployment in the production deploy script. Any attacker who monitors the mempool can frontrun this call and inject a malicious `BridgeConfig` address, permanently corrupting the bridge's token-address registry and chain-ID validation before any assets are bridged.

---

### Finding Description

`BridgeCommittee.initializeConfig()` is declared `external` with no ownership or role guard — only a one-time check that `config == address(0)`:

```solidity
// bridge/evm/contracts/BridgeCommittee.sol  lines 63-66
function initializeConfig(address _config) external {
    require(address(config) == address(0), "BridgeCommittee: Config already initialized");
    config = IBridgeConfig(_config);
}
``` [1](#0-0) 

The production deploy script (`deploy_bridge.s.sol`) runs under `vm.startBroadcast()`, which submits **each call as a separate on-chain transaction**. The sequence is:

| Tx | Action |
|----|--------|
| 1 | `deployUUPSProxy("BridgeCommittee.sol", abi.encodeCall(BridgeCommittee.initialize, (...)))` — atomic, safe |
| 2 | `deployUUPSProxy("BridgeConfig.sol", abi.encodeCall(BridgeConfig.initialize, (...)))` |
| 3 | `BridgeCommittee(bridgeCommittee).initializeConfig(address(bridgeConfig))` ← **frontrunnable** | [2](#0-1) 

Between Tx 2 and Tx 3 there is an open window. An attacker watching the mempool can submit a higher-gas call to `initializeConfig(maliciousConfigAddress)` before the deployer's Tx 3 lands. Once the attacker's transaction is mined first, the deployer's Tx 3 reverts with `"BridgeCommittee: Config already initialized"`, and the bridge is permanently bound to the malicious config.

---

### Impact Explanation

`SuiBridge.transferBridgedTokensWithSignatures` reads the config from the committee on every token-transfer execution:

```solidity
// bridge/evm/contracts/SuiBridge.sol  lines 67-82
IBridgeConfig config = committee.config();
...
require(tokenTransferPayload.targetChain == config.chainID(), ...);
uint256 erc20AdjustedAmount = BridgeUtils.convertSuiToERC20Decimal(
    IERC20Metadata(config.tokenAddressOf(tokenTransferPayload.tokenID)).decimals(),
    config.tokenSuiDecimalOf(tokenTransferPayload.tokenID),
    tokenTransferPayload.amount
);
_transferTokensFromVault(..., tokenTransferPayload.tokenID, ...);
``` [3](#0-2) 

The `onlySupportedChain` modifier (applied to every inbound transfer) also delegates to `committee.config().isChainSupported(chainID)`.

A malicious `BridgeConfig` can:

1. **Permanent fund lock (High)** — return `false` for every `isChainSupported()` call, blocking all inbound token releases from the vault forever.
2. **Illegitimate unlock (Critical)** — return attacker-controlled ERC-20 addresses from `tokenAddressOf()`, redirecting vault withdrawals to a token contract the attacker controls, or return a `chainID` that accepts replayed messages from other chains, enabling double-spend unlocks.

Both outcomes satisfy the HackenProof Sui bridge impact gate (permanent fund lock → High; bridge governance bypass enabling illegitimate unlock → Critical).

---

### Likelihood Explanation

- The deployer's `initializeConfig` call is broadcast as a plain public Ethereum transaction, visible in the mempool before inclusion.
- The attacker needs only to call the same function with a higher gas price — no special privilege, no committee membership, no cryptographic material.
- The `BridgeCommittee` proxy address is deterministic (CREATE2 or sequential nonce) and can be predicted or observed from Tx 1.
- Likelihood: **Medium-High** (standard mempool frontrun on a known target function with no time pressure).

---

### Recommendation

Merge `initializeConfig` into the `initialize` call so both steps execute atomically in a single transaction:

```solidity
function initialize(
    address[] memory committee,
    uint16[] memory stake,
    uint16 minStakeRequired,
    address _config          // add config here
) external initializer {
    __CommitteeUpgradeable_init(address(this));
    __UUPSUpgradeable_init();
    // ... existing stake setup ...
    config = IBridgeConfig(_config);
}
```

Alternatively, restrict `initializeConfig` to the deployer or a trusted admin role:

```solidity
function initializeConfig(address _config) external onlyOwner {
    require(address(config) == address(0), "BridgeCommittee: Config already initialized");
    config = IBridgeConfig(_config);
}
```

The deploy script should also be updated to pass the config address directly to `initialize` so no separate transaction is needed.

---

### Proof of Concept

```
// Setup: attacker monitors mempool and sees deployer's pending Tx 3
// (BridgeCommittee.initializeConfig(legitimateConfig))

// Attacker deploys a malicious config that:
//   - returns false for all isChainSupported() calls
//   - returns address(0) for all tokenAddressOf() calls
contract MaliciousConfig is IBridgeConfig {
    function chainID() external pure returns (uint8) { return 0; }
    function isChainSupported(uint8) external pure returns (bool) { return false; }
    function tokenAddressOf(uint8) external pure returns (address) { return address(0); }
    function tokenSuiDecimalOf(uint8) external pure returns (uint8) { return 0; }
    function tokenPriceOf(uint8) external pure returns (uint64) { return 0; }
    function isTokenSupported(uint8) external pure returns (bool) { return false; }
}

// Attacker frontruns with higher gas:
BridgeCommittee(bridgeCommitteeProxy).initializeConfig(address(maliciousConfig));
// → config is now permanently set to MaliciousConfig
// → deployer's subsequent initializeConfig call reverts: "Config already initialized"

// Result: SuiBridge is deployed pointing to a committee with a malicious config.
// All calls to transferBridgedTokensWithSignatures revert at onlySupportedChain,
// permanently locking all assets in BridgeVault.
``` [1](#0-0) [4](#0-3) [5](#0-4)

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

**File:** bridge/evm/contracts/SuiBridge.sol (L55-89)
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
```
