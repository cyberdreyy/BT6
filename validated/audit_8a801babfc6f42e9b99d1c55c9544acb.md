### Title
Cross-Source-Chain Nonce Collision in `isTransferProcessed` Allows Permanent Fund Lock - (File: `bridge/evm/contracts/SuiBridge.sol`)

### Summary

`SuiBridge.sol` and `SuiBridgeV2.sol` track processed token-transfer messages with a mapping keyed solely by the 64-bit nonce (`message.nonce`). Because the source-chain identifier (`message.chainID`) is excluded from the key, two committee-signed messages originating from **different** source chains that happen to carry the same sequence number share a single slot. Processing the first message permanently marks that nonce as consumed, making the second message unclaimable and locking the user's funds.

### Finding Description

`BridgeUtils.Message` carries both a `chainID` (the source chain) and a `nonce` (the per-source-chain sequence number). The `MessageVerifier.verifyMessageAndSignatures` modifier deliberately skips the chain-ID equality check for `TOKEN_TRANSFER` messages:

```solidity
if (messageType != BridgeUtils.TOKEN_TRANSFER) {
    require(
        message.chainID == committee.config().chainID(),
        "MessageVerifier: Invalid chain ID"
    );
    require(message.nonce == nonces[message.messageType], "MessageVerifier: Invalid nonce");
    nonces[message.messageType]++;
}
``` [1](#0-0) 

For `TOKEN_TRANSFER`, the only replay guard is:

```solidity
mapping(uint64 nonce => bool isProcessed) public isTransferProcessed;
...
require(!isTransferProcessed[message.nonce], "SuiBridge: Message already processed");
...
isTransferProcessed[message.nonce] = true;
``` [2](#0-1) [3](#0-2) [4](#0-3) 

The same pattern is inherited by `SuiBridgeV2`: [5](#0-4) [6](#0-5) 

The key is `uint64 nonce` alone — `message.chainID` (the source chain) is never incorporated. The `onlySupportedChain(message.chainID)` modifier only checks membership in the supported-chain whitelist, not uniqueness: [7](#0-6) 

By contrast, the Move-side `BridgeMessageKey` correctly includes `source_chain` in its composite key:

```move
public struct BridgeMessageKey has copy, drop, store {
    source_chain: u8,
    message_type: u8,
    bridge_seq_num: u64,
}
``` [8](#0-7) 

The EVM side lacks this source-chain dimension in its replay-protection map.

### Impact Explanation

If the `BridgeConfig` supported-chain list includes more than one Sui source chain (e.g., `SUI_MAINNET = 0` and `SUI_TESTNET = 1`), an ordinary user who observes two pending committee-signed `TOKEN_TRANSFER` messages — one from each source chain — both carrying `nonce = N` and targeting the same EVM chain, can submit the Testnet message first. This sets `isTransferProcessed[N] = true`. The Mainnet message is then permanently unprocessable. The user's tokens were already burned on Sui Mainnet by `send_token` but will never be minted on the EVM side — a permanent fund lock matching the High/Medium impact class. [9](#0-8) 

### Likelihood Explanation

Exploitation requires the EVM bridge deployment to have both `SUI_MAINNET` (0) and `SUI_TESTNET` (1) in its supported-chain list simultaneously. Current production deployments pair Ethereum Mainnet with Sui Mainnet only, making the collision unlikely today. However, the code imposes no structural barrier against multi-source-chain configurations, and the chain-ID constants are defined and validated: [10](#0-9) 

Any future configuration change or bridge extension that adds a second Sui source chain immediately activates the vulnerability without any code change.

### Recommendation

Key `isTransferProcessed` by the composite `(sourceChainID, nonce)` pair, mirroring the Move-side `BridgeMessageKey`:

```solidity
// Replace:
mapping(uint64 nonce => bool isProcessed) public isTransferProcessed;

// With:
mapping(uint8 sourceChain => mapping(uint64 nonce => bool isProcessed))
    public isTransferProcessed;
```

Update all reads and writes accordingly:

```solidity
require(!isTransferProcessed[message.chainID][message.nonce], ...);
isTransferProcessed[message.chainID][message.nonce] = true;
```

This aligns the EVM replay-protection domain with the Move-side composite key and eliminates the nonce namespace collision across source chains.

### Proof of Concept

1. Deploy `SuiBridge` with `BridgeConfig` that lists both `SUI_MAINNET` (0) and `SUI_TESTNET` (1) as supported source chains targeting `ETH_MAINNET` (10).
2. User A bridges tokens from Sui Mainnet; the committee signs `Message{chainID:0, nonce:5, targetChain:10, ...}`.
3. Independently, a Sui Testnet transfer also reaches nonce 5; the committee signs `Message{chainID:1, nonce:5, targetChain:10, ...}`.
4. Attacker observes both signed messages and calls `transferBridgedTokensWithSignatures` with the Testnet message first.
5. `isTransferProcessed[5]` is set to `true`.
6. The bridge node's subsequent submission of the Mainnet message reverts with `"SuiBridge: Message already processed"`.
7. User A's tokens are permanently locked — burned on Sui Mainnet, never minted on Ethereum. [2](#0-1) [11](#0-10) [1](#0-0)

### Citations

**File:** bridge/evm/contracts/utils/MessageVerifier.sol (L43-50)
```text
        if (messageType != BridgeUtils.TOKEN_TRANSFER) {
            // verify chain ID
            require(
                message.chainID == committee.config().chainID(), "MessageVerifier: Invalid chain ID"
            );
            require(message.nonce == nonces[message.messageType], "MessageVerifier: Invalid nonce");
            nonces[message.messageType]++;
        }
```

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

**File:** bridge/evm/contracts/SuiBridge.sol (L283-288)
```text
    modifier onlySupportedChain(uint8 targetChainID) {
        require(
            committee.config().isChainSupported(targetChainID),
            "SuiBridge: Target chain not supported"
        );
        _;
```

**File:** bridge/evm/contracts/SuiBridgeV2.sol (L26-26)
```text
        require(!isTransferProcessed[message.nonce], "SuiBridge: Message already processed");
```

**File:** bridge/evm/contracts/SuiBridgeV2.sol (L55-55)
```text
        isTransferProcessed[message.nonce] = true;
```

**File:** crates/sui-framework/packages/bridge/sources/message.move (L40-44)
```text
public struct BridgeMessageKey has copy, drop, store {
    source_chain: u8,
    message_type: u8,
    bridge_seq_num: u64,
}
```

**File:** crates/sui-framework/packages/bridge/sources/chain_ids.move (L7-13)
```text
const SUI_MAINNET: u8 = 0;
const SUI_TESTNET: u8 = 1;
const SUI_CUSTOM: u8 = 2;

const ETH_MAINNET: u8 = 10;
const ETH_SEPOLIA: u8 = 11;
const ETH_CUSTOM: u8 = 12;
```
