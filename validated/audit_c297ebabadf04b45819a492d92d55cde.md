### Title
Token Transfer Nonce Keyed Without Source Chain ID Enables Cross-Chain Nonce Collision and Permanent Fund Lock — (`bridge/evm/contracts/SuiBridge.sol` + `bridge/evm/contracts/utils/MessageVerifier.sol`)

---

### Summary

The `verifyMessageAndSignatures` modifier in `MessageVerifier.sol` explicitly skips chain ID validation for `TOKEN_TRANSFER` messages. The replay-protection mapping in `SuiBridge.sol` is keyed only by `message.nonce` (`uint64`), not by `(message.chainID, message.nonce)`. When the bridge is configured to accept transfers from more than one source chain, nonces from different source chains collide in this mapping, permanently locking funds from whichever source chain submits second.

---

### Finding Description

In `MessageVerifier.sol`, the `verifyMessageAndSignatures` modifier applies chain ID and sequential-nonce validation only for non-`TOKEN_TRANSFER` message types:

```solidity
if (messageType != BridgeUtils.TOKEN_TRANSFER) {
    // verify chain ID
    require(
        message.chainID == committee.config().chainID(), "MessageVerifier: Invalid chain ID"
    );
    require(message.nonce == nonces[message.messageType], "MessageVerifier: Invalid nonce");
    nonces[message.messageType]++;
}
``` [1](#0-0) 

For `TOKEN_TRANSFER` messages, neither the chain ID check nor the sequential nonce check runs. The only replay guard is in `SuiBridge.sol`:

```solidity
mapping(uint64 nonce => bool isProcessed) public isTransferProcessed;
...
require(!isTransferProcessed[message.nonce], "SuiBridge: Message already processed");
...
isTransferProcessed[message.nonce] = true;
``` [2](#0-1) [3](#0-2) [4](#0-3) 

The key is a bare `uint64` nonce. The `onlySupportedChain` modifier only checks whether the source chain is in the supported-chains list, not that it equals a single expected source chain:

```solidity
modifier onlySupportedChain(uint8 targetChainID) {
    require(
        committee.config().isChainSupported(targetChainID),
        "SuiBridge: Target chain not supported"
    );
    _;
}
``` [5](#0-4) 

The same pattern is present in `SuiBridgeV2.transferBridgedTokensWithSignaturesV2`, which inherits the same `isTransferProcessed` mapping and the same `verifyMessageAndSignatures` modifier: [6](#0-5) 

The `BridgeUtils.Message` struct carries a `chainID` field that identifies the source chain, but it is never compared against the contract's own chain ID for token transfers: [7](#0-6) 

---

### Impact Explanation

When the bridge config lists more than one source chain as supported (e.g., `SUI_MAINNET` = 0 and `SUI_TESTNET` = 1, or any two Sui/Eth chain IDs), both chains maintain independent nonce sequences starting at 0. Because `isTransferProcessed` is keyed only by `nonce`:

- Source chain A processes transfer with nonce = N → `isTransferProcessed[N] = true`.
- Source chain B submits a valid, committee-signed transfer with nonce = N → reverts with `"SuiBridge: Message already processed"`.
- The tokens locked on source chain B's vault can never be released on this contract. **Permanent fund lock.**

Additionally, because chain ID is not validated for token transfers, a committee-signed message originally intended for one deployment (e.g., Ethereum mainnet bridge) can be submitted to another deployment (e.g., Ethereum Sepolia bridge) if the source chain ID appears in that deployment's supported-chains list, constituting cross-chain message replay.

This matches the allowed impact: **permanent fund lock** reachable from public (bridge-user) input.

---

### Likelihood Explanation

- Any ordinary bridge user initiating a transfer from a second supported source chain whose nonce counter has reached a value already consumed by the first source chain triggers the lock.
- The Sui bridge committee signs for multiple chain pairs; the same validator set is active across mainnet and testnet deployments, making cross-deployment replay feasible whenever chain IDs overlap in the supported-chains config.
- No privileged access is required; the attacker only needs to submit a valid committee-signed token transfer message.

---

### Recommendation

1. **Key `isTransferProcessed` by `(sourceChainID, nonce)`** instead of bare nonce:
   ```solidity
   mapping(uint8 => mapping(uint64 => bool)) public isTransferProcessed;
   // check: require(!isTransferProcessed[message.chainID][message.nonce], ...)
   // set:   isTransferProcessed[message.chainID][message.nonce] = true;
   ```

2. **Remove the `TOKEN_TRANSFER` exception** in `verifyMessageAndSignatures` and validate `message.chainID == committee.config().chainID()` for all message types, or add an equivalent check directly in `transferBridgedTokensWithSignatures` / `transferBridgedTokensWithSignaturesV2`.

3. Apply the same fix to `SuiBridgeV2` which inherits the same mapping and modifier.

---

### Proof of Concept

Setup: Ethereum mainnet bridge configured with both `SUI_MAINNET` (chainID=0) and `SUI_TESTNET` (chainID=1) as supported source chains (both pass `onlySupportedChain`).

1. Alice deposits tokens on Sui mainnet. Validators produce a signed `TOKEN_TRANSFER` message: `{chainID: 0, nonce: 5, ...}`. Alice calls `transferBridgedTokensWithSignatures(sigs, msg)`. Succeeds; `isTransferProcessed[5] = true`.

2. Bob deposits tokens on Sui testnet. Validators produce a signed `TOKEN_TRANSFER` message: `{chainID: 1, nonce: 5, ...}`. Bob calls `transferBridgedTokensWithSignatures(sigs, msg)`.

3. Inside `verifyMessageAndSignatures`: message type matches, signatures are valid, chain ID check is **skipped** (TOKEN_TRANSFER branch). Execution reaches `require(!isTransferProcessed[5], ...)` → **reverts** with `"SuiBridge: Message already processed"`.

4. Bob's tokens are permanently locked in the Sui testnet bridge vault. No path exists to claim them on this contract.

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

**File:** bridge/evm/contracts/SuiBridge.sol (L65-65)
```text
        require(!isTransferProcessed[message.nonce], "SuiBridge: Message already processed");
```

**File:** bridge/evm/contracts/SuiBridge.sol (L92-92)
```text
        isTransferProcessed[message.nonce] = true;
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

**File:** bridge/evm/contracts/SuiBridgeV2.sol (L16-55)
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
```

**File:** bridge/evm/contracts/utils/BridgeUtils.sol (L18-24)
```text
    struct Message {
        uint8 messageType;
        uint8 version;
        uint64 nonce;
        uint8 chainID;
        bytes payload;
    }
```
