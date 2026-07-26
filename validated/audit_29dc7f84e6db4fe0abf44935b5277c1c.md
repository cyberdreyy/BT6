### Title
Unprotected `initializeConfig` Allows Any Caller to Inject a Malicious `IBridgeConfig` into `BridgeCommittee` — (`File: bridge/evm/contracts/BridgeCommittee.sol`)

---

### Summary

`BridgeCommittee.initializeConfig` carries no caller restriction. Any address can call it during the window between proxy deployment and the deployer's own call, permanently injecting a malicious `IBridgeConfig` contract. Because every downstream bridge operation — signature-stake thresholds, token-address resolution, chain-ID validation — is routed through `committee.config()`, a poisoned config enables full bridge governance bypass and illegitimate token unlock.

---

### Finding Description

`BridgeCommittee.sol` exposes a two-step initialisation pattern. The first step, `initialize`, is protected by OpenZeppelin's `initializer` modifier. The second step, `initializeConfig`, is completely unguarded:

```solidity
// bridge/evm/contracts/BridgeCommittee.sol  L63-66
function initializeConfig(address _config) external {
    require(address(config) == address(0), "BridgeCommittee: Config already initialized");
    config = IBridgeConfig(_config);
}
```

The only protection is the "already initialised" guard, which is itself the race condition: the first caller wins. The deployment script (`deploy_bridge.s.sol`) issues `initialize` and `initializeConfig` as separate transactions, creating a front-runnable window:

```
Tx 1 – deployer: BridgeCommittee proxy.initialize(committee, stake, minStake)
                                                    ← attack window ←
Tx 2 – deployer: BridgeCommittee proxy.initializeConfig(legitimateConfig)
```

An attacker who observes Tx 1 in the mempool can submit `initializeConfig(maliciousConfig)` with a higher gas price, winning the race. The deployer's Tx 2 then reverts with "Config already initialized", and the malicious config is permanently locked in.

The deployment script also calls `initializeConfig` on the bare implementation contract:

```solidity
// bridge/evm/script/deploy_bridge.s.sol  L176-178
BridgeCommittee committeeImplementation =
    BridgeCommittee(Upgrades.getImplementationAddress(bridgeCommittee));
committeeImplementation.initializeConfig(address(bridgeConfig));
```

This call on the implementation is equally unprotected and can be front-run independently.

---

### Impact Explanation

Every security-critical path in the bridge reads `committee.config()`:

- **`verifySignatures`** calls `BridgeUtils.requiredStake(message)`, which queries the config for the required stake threshold. A malicious config returning `requiredStake = 0` causes `verifySignatures` to pass with zero valid signatures, allowing anyone to forge a `TOKEN_TRANSFER` message and drain the vault.
- **`transferBridgedTokensWithSignatures`** in `SuiBridge` calls `config.tokenAddressOf(tokenID)` to resolve the ERC-20 address. A malicious config can redirect withdrawals to attacker-controlled token contracts.
- **`onlySupportedChain`** calls `config.isChainSupported()`. A malicious config can return `true` for any chain ID, bypassing chain-domain separation.

The worst-case path — `requiredStake = 0` — lets an attacker submit a `TOKEN_TRANSFER` message with an empty `signatures` array, pass `verifySignatures`, and unlock the entire vault balance in a single transaction. This is a bridge governance bypass enabling illegitimate token unlock, matching the Critical impact gate.

---

### Likelihood Explanation

The attack requires front-running a deployment transaction on Ethereum mainnet, which is straightforward with MEV infrastructure. The window exists in every fresh deployment and every re-deployment after an upgrade. No special privilege, stake, or committee membership is required — any EOA can call `initializeConfig`.

---

### Recommendation

Add an `onlyOwner` (or equivalent deployer-only) modifier to `initializeConfig`, or collapse both initialisation steps into a single atomic `initialize` call:

```solidity
// Recommended: single atomic initializer
function initialize(
    address[] memory committee,
    uint16[] memory stake,
    uint16 minStakeRequired,
    address _config          // ← add config here
) external initializer {
    __CommitteeUpgradeable_init(address(this));
    __UUPSUpgradeable_init();
    // ... existing stake setup ...
    require(_config != address(0), "BridgeCommittee: zero config address");
    config = IBridgeConfig(_config);
}
```

If the two-step pattern must be kept, restrict `initializeConfig` to the deployer address recorded during `initialize`:

```solidity
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

1. Deployer broadcasts `BridgeCommittee.initialize(committee, stake, minStake)` — Tx A lands in the mempool.
2. Attacker deploys `MaliciousConfig` with `requiredStake()` returning `0` for all message types.
3. Attacker front-runs with `BridgeCommittee(proxy).initializeConfig(address(maliciousConfig))` at higher gas — this succeeds because `config == address(0)`.
4. Deployer's `initializeConfig(legitimateConfig)` reverts: "Config already initialized".
5. Attacker calls `SuiBridge.transferBridgedTokensWithSignatures([], forgedMessage)`:
   - `verifySignatures` calls `BridgeUtils.requiredStake(forgedMessage)` → `0`.
   - Loop over zero signatures passes; `approvalStake (0) >= requiredStake (0)` → passes.
   - `isTransferProcessed[nonce]` is `false` → passes.
   - `_transferTokensFromVault` sends vault tokens to attacker's address. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** bridge/evm/contracts/BridgeCommittee.sol (L30-57)
```text
    function initialize(address[] memory committee, uint16[] memory stake, uint16 minStakeRequired)
        external
        initializer
    {
        __CommitteeUpgradeable_init(address(this));
        __UUPSUpgradeable_init();

        uint256 _committeeLength = committee.length;

        require(_committeeLength < 256, "BridgeCommittee: Committee length must be less than 256");

        require(
            _committeeLength == stake.length,
            "BridgeCommittee: Committee and stake arrays must be of the same length"
        );

        uint16 totalStake;
        for (uint16 i; i < _committeeLength; i++) {
            require(
                committeeStake[committee[i]] == 0, "BridgeCommittee: Duplicate committee member"
            );
            committeeStake[committee[i]] = stake[i];
            committeeIndex[committee[i]] = uint8(i);
            totalStake += stake[i];
        }

        require(totalStake >= minStakeRequired, "BridgeCommittee: total stake is less than minimum"); // 10000 == 100%
    }
```

**File:** bridge/evm/contracts/BridgeCommittee.sol (L63-66)
```text
    function initializeConfig(address _config) external {
        require(address(config) == address(0), "BridgeCommittee: Config already initialized");
        config = IBridgeConfig(_config);
    }
```

**File:** bridge/evm/contracts/BridgeCommittee.sol (L75-106)
```text
    function verifySignatures(bytes[] memory signatures, BridgeUtils.Message memory message)
        external
        view
        override
    {
        uint32 requiredStake = BridgeUtils.requiredStake(message);

        uint16 approvalStake;
        address signer;
        uint256 bitmap;

        // Check validity of each signature and aggregate the approval stake
        for (uint16 i; i < signatures.length; i++) {
            bytes memory signature = signatures[i];
            // recover the signer from the signature
            (bytes32 r, bytes32 s, uint8 v) = splitSignature(signature);

            (signer,,) = ECDSA.tryRecover(BridgeUtils.computeHash(message), v, r, s);

            require(!blocklist[signer], "BridgeCommittee: Signer is blocklisted");
            require(committeeStake[signer] > 0, "BridgeCommittee: Signer has no stake");

            uint8 index = committeeIndex[signer];
            uint256 mask = 1 << index;
            require(bitmap & mask == 0, "BridgeCommittee: Duplicate signature provided");
            bitmap |= mask;

            approvalStake += committeeStake[signer];
        }

        require(approvalStake >= requiredStake, "BridgeCommittee: Insufficient stake amount");
    }
```

**File:** bridge/evm/script/deploy_bridge.s.sol (L121-178)
```text
        Options memory opts;
        opts.unsafeSkipAllChecks = true;

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
