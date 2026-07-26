Looking at the `isTransferProcessed` mapping in `SuiBridge.sol` and the broader bridge architecture to evaluate whether the external report's root cause (loose/non-unique identifier matching across chains) has a live analog.

The deployment config `31337.json` confirms `supportedChainIds: [1, 2, 3]` — three independent source chains simultaneously supported by one EVM bridge contract. Each source chain has its own nonce counter starting from 0. The `isTransferProcessed` mapping keys only on `nonce`, not `(chainID, nonce)`. This is the exact analog of the external report's root cause.

---

### Title
Cross-Source-Chain Nonce Collision Causes Permanent Fund Lock in `isTransferProcessed` - (File: `bridge/evm/contracts/SuiBridge.sol`)

### Summary
`SuiBridge.sol` tracks processed token transfers with `mapping(uint64 nonce => bool isProcessed) public isTransferProcessed`. The key is the bare `message.nonce` with no domain separation by `message.chainID`. Because the bridge is explicitly configured to support multiple source chains simultaneously (e.g., `supportedChainIds: [1, 2, 3]` in the deployment config), each source chain maintains its own independent nonce counter starting from 0. When two different source chains both reach the same nonce value, the second legitimate transfer is permanently rejected as "already processed," locking the user's funds with no recovery path.

### Finding Description
In `transferBridgedTokensWithSignatures` (and identically in `transferBridgedTokensWithSignaturesV2`):

```solidity
// SuiBridge.sol line 24
mapping(uint64 nonce => bool isProcessed) public isTransferProcessed;

// line 65
require(!isTransferProcessed[message.nonce], "SuiBridge: Message already processed");

// line 92
isTransferProcessed[message.nonce] = true;
```

The `message.chainID` field identifies the source chain, but it is never included in the processed-transfer key. The `BridgeConfig` explicitly supports multiple source chains via `mapping(uint8 chainId => bool isSupported) public supportedChains`, and the deployment configuration (`31337.json`, e2e test utils) initializes the bridge with `supportedChainIds: [1, 2, 3]`. Each source chain emits nonces sequentially from 0 independently. The collision is therefore not a theoretical edge case — it is a deterministic outcome as transfer volume grows across chains.

The `BridgeMessageKey` used in the Move-side bridge correctly includes `(source_chain, message_type, bridge_seq_num)` as a composite key, demonstrating that the protocol designers understood the need for domain separation. The EVM side omits the source chain from the key. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

### Impact Explanation
When source chain A processes nonce N and source chain B later submits a committee-signed message also carrying nonce N, the call reverts with `"SuiBridge: Message already processed"`. The user's tokens were already locked or burned on the source chain. There is no mechanism to re-submit with a different nonce (nonces are assigned by the bridge protocol, not the user). The funds are permanently locked in the vault with no recovery path. This matches the "permanent fund lock" impact class. [6](#0-5) [7](#0-6) 

### Likelihood Explanation
Nonces are sequential integers starting from 0 on each source chain. With three supported source chains, the first collision occurs as soon as any two chains both reach the same nonce value — guaranteed to happen within the first few hundred transfers across chains. No attacker action is required; normal bridge usage by ordinary users produces the collision. The trigger is fully public and unprivileged. [8](#0-7) [9](#0-8) 

### Recommendation
Change the `isTransferProcessed` mapping to include the source chain ID as part of the key:

```solidity
// Before
mapping(uint64 nonce => bool isProcessed) public isTransferProcessed;
require(!isTransferProcessed[message.nonce], ...);
isTransferProcessed[message.nonce] = true;

// After
mapping(uint8 chainID => mapping(uint64 nonce => bool isProcessed)) public isTransferProcessed;
require(!isTransferProcessed[message.chainID][message.nonce], ...);
isTransferProcessed[message.chainID][message.nonce] = true;
```

Apply the same fix to `SuiBridgeV2.sol`. This mirrors the correct composite key `(source_chain, message_type, bridge_seq_num)` already used in the Move bridge's `BridgeMessageKey`. [10](#0-9) 

### Proof of Concept
1. Deploy the bridge with `supportedChainIds: [1, 2]` (two source chains).
2. Source chain 1 user bridges tokens → committee signs message `{chainID: 1, nonce: 0, ...}` → relayer calls `transferBridgedTokensWithSignatures` → succeeds → `isTransferProcessed[0] = true`.
3. Source chain 2 user bridges tokens → committee signs message `{chainID: 2, nonce: 0, ...}` → relayer calls `transferBridgedTokensWithSignatures` → **reverts** with `"SuiBridge: Message already processed"` because `isTransferProcessed[0]` is already `true`.
4. Source chain 2 user's tokens are permanently locked. No retry is possible because the nonce is protocol-assigned and the contract has no override path. [11](#0-10) [12](#0-11)

### Citations

**File:** bridge/evm/contracts/SuiBridge.sol (L24-24)
```text
    mapping(uint64 nonce => bool isProcessed) public isTransferProcessed;
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

**File:** bridge/evm/contracts/BridgeConfig.sol (L18-18)
```text
    mapping(uint8 chainId => bool isSupported) public supportedChains;
```

**File:** bridge/evm/deploy_configs/31337.json (L5-7)
```json
  "sourceChainId": 12,
  "supportedChainIds": [1, 2, 3],
  "supportedChainLimitsInDollars": [1000000000000000, 1000000000000000, 1000000000000000],
```

**File:** bridge/evm/contracts/SuiBridgeV2.sol (L16-66)
```text
    function transferBridgedTokensWithSignaturesV2(
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
        require(message.version == 2, "SuiBridge: Invalid message version");

        IBridgeConfig config = committee.config();

        BridgeUtilsV2.TokenTransferPayloadV2 memory tokenTransferPayload =
            BridgeUtilsV2.decodeTokenTransferPayloadV2(message.payload);

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
            erc20AdjustedAmount,
            tokenTransferPayload.timestampMs / 1000
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

**File:** crates/sui-bridge/src/e2e_tests/test_utils.rs (L612-614)
```rust
        source_chain_id: 12,
        supported_chain_ids: vec![1, 2, 3],
        supported_chain_limits_in_dollars: vec![
```

**File:** crates/sui-framework/packages/bridge/sources/message.move (L40-44)
```text
public struct BridgeMessageKey has copy, drop, store {
    source_chain: u8,
    message_type: u8,
    bridge_seq_num: u64,
}
```
