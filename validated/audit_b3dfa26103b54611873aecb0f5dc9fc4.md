### Title
Cross-Source-Chain Nonce Collision in `isTransferProcessed` Permanently Locks Bridge Funds - (`File: bridge/evm/contracts/SuiBridge.sol`)

---

### Summary

`SuiBridge.sol` (and `SuiBridgeV2.sol`) tracks processed inbound token transfers using `mapping(uint64 nonce => bool isProcessed) public isTransferProcessed`, keyed only by `message.nonce` without including `message.chainID`. When the bridge is configured to accept transfers from multiple source chains (e.g., `SUI_TESTNET` and `SUI_CUSTOM` both routing to `ETH_SEPOLIA`), two legitimate transfers from different source chains that happen to share the same nonce will collide: whichever is processed first marks `isTransferProcessed[N] = true`, permanently blocking the other. The tokens on the second source chain are already burned/locked and can never be claimed on Ethereum.

---

### Finding Description

In `SuiBridge.sol`, the replay-protection mapping is declared as:

```solidity
mapping(uint64 nonce => bool isProcessed) public isTransferProcessed;
``` [1](#0-0) 

In `transferBridgedTokensWithSignatures`, the check and write are:

```solidity
require(!isTransferProcessed[message.nonce], "SuiBridge: Message already processed");
// ...
isTransferProcessed[message.nonce] = true;
``` [2](#0-1) 

The same pattern is repeated verbatim in `SuiBridgeV2.transferBridgedTokensWithSignaturesV2`: [3](#0-2) 

The `BridgeUtils.Message` struct carries both `chainID` (the source chain) and `nonce` (the per-source-chain sequence number). The `onlySupportedChain(message.chainID)` modifier only validates that the source chain is in the supported set; it does not prevent two different source chains from sharing the same nonce value. [4](#0-3) 

Critically, the `verifyMessageAndSignatures` modifier in `MessageVerifier.sol` explicitly **skips** the nonce check for `TOKEN_TRANSFER` messages:

```solidity
if (messageType != BridgeUtils.TOKEN_TRANSFER) {
    require(message.nonce == nonces[message.messageType], "MessageVerifier: Invalid nonce");
    nonces[message.messageType]++;
}
``` [5](#0-4) 

So the only replay guard for token transfers is `isTransferProcessed[message.nonce]`, which is blind to the source chain.

The bridge is designed to support multiple source chains. The deploy configuration explicitly lists multiple supported chain IDs: [6](#0-5) 

The Move-side `chain_ids.move` defines valid routes where multiple Sui chains (`SUI_TESTNET`, `SUI_CUSTOM`) can both route to the same EVM destination (`ETH_SEPOLIA`, `ETH_CUSTOM`): [7](#0-6) 

Each Sui chain maintains its own independent nonce counter (`sequence_nums: VecMap<u8, u64>` in `BridgeInner`), starting from 0: [8](#0-7) 

When both `SUI_TESTNET` (chainID=1) and `SUI_CUSTOM` (chainID=2) each reach nonce=N and both route to the same `ETH_SEPOLIA` bridge instance, the first transfer processed marks `isTransferProcessed[N] = true`. The second transfer — carrying a different `message.chainID` but the same `message.nonce` — is rejected with `"SuiBridge: Message already processed"`, even though it is a completely distinct, valid transfer.

---

### Impact Explanation

Tokens are burned or locked on the source Sui chain at the time `send_token` is called. If the corresponding EVM claim is permanently blocked, those tokens are irrecoverably lost. This is a **permanent fund lock** matching the High/Medium impact class. The amount locked equals the full value of every transfer from the second source chain whose nonce collides with an already-processed nonce from the first source chain.

---

### Likelihood Explanation

The collision is **inevitable** once the bridge is configured with two or more Sui source chains routing to the same EVM destination. Both chains start their nonce counters at 0 and increment monotonically. Nonce 0 collides immediately. An ordinary user who submits a valid transfer from chain A with nonce N will inadvertently (or deliberately) block any transfer from chain B with the same nonce N. No privileged access, leaked keys, or malicious validator is required — only the ability to call `transferBridgedTokensWithSignatures` with a committee-signed message, which is the normal bridge claim flow.

---

### Recommendation

Key `isTransferProcessed` by `(sourceChainID, nonce)` instead of `nonce` alone:

```solidity
// Before
mapping(uint64 nonce => bool isProcessed) public isTransferProcessed;

// After
mapping(uint8 chainID => mapping(uint64 nonce => bool isProcessed)) public isTransferProcessed;
```

Update both the check and the write in `transferBridgedTokensWithSignatures` (and `transferBridgedTokensWithSignaturesV2`):

```solidity
require(!isTransferProcessed[message.chainID][message.nonce], "SuiBridge: Message already processed");
// ...
isTransferProcessed[message.chainID][message.nonce] = true;
``` [1](#0-0) [3](#0-2) 

---

### Proof of Concept

**Setup:** Deploy `SuiBridge.sol` on `ETH_SEPOLIA` (chainID=3) with `supportedChains` containing both `SUI_TESTNET` (chainID=1) and `SUI_CUSTOM` (chainID=2).

**Step 1:** User A bridges tokens from `SUI_TESTNET` → `ETH_SEPOLIA`. This is the first-ever transfer on that chain, so `nonce = 0`. Bridge nodes sign the message `{chainID: 1, nonce: 0, ...}`. User A calls `transferBridgedTokensWithSignatures`. `isTransferProcessed[0]` is set to `true`. Tokens are released to User A.

**Step 2:** User B bridges tokens from `SUI_CUSTOM` → `ETH_SEPOLIA`. This is also the first-ever transfer on that chain, so `nonce = 0`. Bridge nodes sign the message `{chainID: 2, nonce: 0, ...}`. User B calls `transferBridgedTokensWithSignatures`.

**Result:** The call reverts with `"SuiBridge: Message already processed"` because `isTransferProcessed[0]` is already `true`. User B's tokens were burned on `SUI_CUSTOM` and are permanently lost. The `onlySupportedChain` modifier passes (chainID=2 is supported), committee signatures are valid, but the nonce check fails because it ignores the source chain dimension. [9](#0-8) [5](#0-4)

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

**File:** bridge/evm/contracts/SuiBridgeV2.sol (L26-55)
```text
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
```

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

**File:** bridge/evm/deploy_configs/31337.json (L6-6)
```json
  "supportedChainIds": [1, 2, 3],
```

**File:** crates/sui-framework/packages/bridge/sources/chain_ids.move (L66-79)
```text
public fun valid_routes(): vector<BridgeRoute> {
    vector[
        BridgeRoute { source: SUI_MAINNET, destination: ETH_MAINNET },
        BridgeRoute { source: ETH_MAINNET, destination: SUI_MAINNET },
        BridgeRoute { source: SUI_TESTNET, destination: ETH_SEPOLIA },
        BridgeRoute { source: SUI_TESTNET, destination: ETH_CUSTOM },
        BridgeRoute { source: SUI_CUSTOM, destination: ETH_CUSTOM },
        BridgeRoute { source: SUI_CUSTOM, destination: ETH_SEPOLIA },
        BridgeRoute { source: ETH_SEPOLIA, destination: SUI_TESTNET },
        BridgeRoute { source: ETH_SEPOLIA, destination: SUI_CUSTOM },
        BridgeRoute { source: ETH_CUSTOM, destination: SUI_TESTNET },
        BridgeRoute { source: ETH_CUSTOM, destination: SUI_CUSTOM },
    ]
}
```

**File:** crates/sui-framework/packages/bridge/sources/bridge.move (L51-65)
```text
public struct BridgeInner has store {
    bridge_version: u64,
    message_version: u8,
    chain_id: u8,
    // nonce for replay protection
    // key: message type, value: next sequence number
    sequence_nums: VecMap<u8, u64>,
    // committee
    committee: BridgeCommittee,
    // Bridge treasury for mint/burn bridged tokens
    treasury: BridgeTreasury,
    token_transfer_records: LinkedTable<BridgeMessageKey, BridgeRecord>,
    limiter: TransferLimiter,
    paused: bool,
}
```
