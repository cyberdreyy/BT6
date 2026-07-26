### Title
Nonce-Only Replay Guard in `isTransferProcessed` Collides Across Multiple Source Chains, Permanently Locking Bridged Funds — (File: `bridge/evm/contracts/SuiBridge.sol`)

---

### Summary

`SuiBridge.sol` tracks processed token transfers with `mapping(uint64 nonce => bool isProcessed) public isTransferProcessed`. The key is **only the nonce**, with no source-chain dimension. The protocol's own hardcoded valid routes send both `SUI_TESTNET` (chain\_id = 1) and `SUI_CUSTOM` (chain\_id = 2) to `ETH_SEPOLIA`, and both `SUI_CUSTOM` and `SUI_TESTNET` to `ETH_CUSTOM`. Each source chain maintains an independent, zero-based nonce counter. When both chains have emitted their N-th transfer to the same EVM destination, the first claim sets `isTransferProcessed[N] = true`; the second legitimate claim is permanently rejected. Tokens are burned on the Sui side with no refund path, causing irreversible fund loss.

---

### Finding Description

**Root cause — packed identifier with missing domain dimension**

The replay-protection map is declared as:

```solidity
mapping(uint64 nonce => bool isProcessed) public isTransferProcessed;
``` [1](#0-0) 

Both `transferBridgedTokensWithSignatures` (V1) and `transferBridgedTokensWithSignaturesV2` (V2) check and set this map using only `message.nonce`:

```solidity
require(!isTransferProcessed[message.nonce], "SuiBridge: Message already processed");
// ...
isTransferProcessed[message.nonce] = true;
``` [2](#0-1) [3](#0-2) 

`message.chainID` (the source chain) is present in the message struct and is verified by `onlySupportedChain` and by the committee signatures, but it is **never incorporated into the deduplication key**.

**Why multiple source chains share the same EVM destination**

The protocol's own `chain_ids.move` hardcodes:

```
BridgeRoute { source: SUI_TESTNET, destination: ETH_SEPOLIA },
BridgeRoute { source: SUI_CUSTOM,  destination: ETH_SEPOLIA },
BridgeRoute { source: SUI_TESTNET, destination: ETH_CUSTOM  },
BridgeRoute { source: SUI_CUSTOM,  destination: ETH_CUSTOM  },
``` [4](#0-3) 

Both `SUI_TESTNET` and `SUI_CUSTOM` are therefore valid source chains for a single EVM bridge deployment (`ETH_SEPOLIA` or `ETH_CUSTOM`). The `onlySupportedChain` modifier accepts both:

```solidity
modifier onlySupportedChain(uint8 targetChainID) {
    require(committee.config().isChainSupported(targetChainID), ...);
    _;
}
``` [5](#0-4) 

**Independent nonce counters guarantee eventual collision**

On the Sui side, each source chain has its own independent sequence counter (`sequence_nums: VecMap<u8, u64>`): [6](#0-5) 

Both `SUI_TESTNET` and `SUI_CUSTOM` start at nonce 0 and increment independently. Their nonce spaces are identical: `{0, 1, 2, …}`. The first transfer from either chain sets `isTransferProcessed[0] = true`; the first transfer from the other chain with nonce 0 is then permanently rejected.

**No refund path**

When a user bridges from Sui, tokens are burned/locked on-chain before the EVM claim is attempted. There is no mechanism in `bridge.move` to reverse a burn if the EVM claim fails. The `claim_token_internal` function only mints on success; a rejected EVM `require` leaves the Sui-side burn irreversible. [7](#0-6) 

---

### Impact Explanation

A legitimate bridge user whose transfer nonce collides with a previously processed transfer from a different source chain loses their tokens permanently. The EVM vault holds the funds but the contract will never release them for that nonce. This is a **permanent fund lock** matching the High/Medium impact class in the allowed gate.

---

### Likelihood Explanation

The collision is **inevitable**, not probabilistic. Both `SUI_TESTNET` and `SUI_CUSTOM` start at nonce 0. The very first transfer from each chain to the same EVM destination produces nonce 0. Whichever arrives second is permanently blocked. No attacker action is required; ordinary bridge users on two different source chains trigger the condition through normal usage.

---

### Recommendation

Key `isTransferProcessed` by the composite `(sourceChainID, nonce)` pair:

```solidity
mapping(uint8 chainID => mapping(uint64 nonce => bool)) public isTransferProcessed;

// check:
require(!isTransferProcessed[message.chainID][message.nonce], "SuiBridge: Message already processed");
// set:
isTransferProcessed[message.chainID][message.nonce] = true;
```

Apply the same change to `SuiBridgeV2.transferBridgedTokensWithSignaturesV2`, which inherits the same mapping. [8](#0-7) 

---

### Proof of Concept

**Setup:** ETH\_SEPOLIA bridge deployment with `BridgeConfig` listing both `SUI_TESTNET` (chain\_id = 1) and `SUI_CUSTOM` (chain\_id = 2) as supported source chains (consistent with the hardcoded valid routes).

**Steps:**

1. User A initiates a token transfer from `SUI_TESTNET` to `ETH_SEPOLIA`. The Sui bridge assigns `seq_num = 0`. Tokens are burned on Sui.

2. The bridge node collects committee signatures over the full message `{messageType=0, version=1, nonce=0, chainID=1, payload=…}` and calls `transferBridgedTokensWithSignatures` on ETH\_SEPOLIA. The call succeeds; `isTransferProcessed[0] = true`.

3. User B initiates a token transfer from `SUI_CUSTOM` to `ETH_SEPOLIA`. The Sui bridge assigns `seq_num = 0` (independent counter). Tokens are burned on Sui.

4. The bridge node collects committee signatures over `{messageType=0, version=1, nonce=0, chainID=2, payload=…}` — a completely different, validly signed message — and calls `transferBridgedTokensWithSignatures` on ETH\_SEPOLIA.

5. The call hits:
   ```solidity
   require(!isTransferProcessed[0], "SuiBridge: Message already processed");
   ```
   `isTransferProcessed[0]` is `true` (set in step 2). The transaction reverts.

6. User B's tokens are permanently lost: burned on Sui, unclaimable on ETH\_SEPOLIA. No retry or refund is possible.

The same scenario applies to `ETH_CUSTOM` (which also accepts both `SUI_TESTNET` and `SUI_CUSTOM`) and to `SuiBridgeV2.transferBridgedTokensWithSignaturesV2`, which reads the same inherited `isTransferProcessed` mapping. [9](#0-8) [10](#0-9) [4](#0-3)

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

**File:** bridge/evm/contracts/SuiBridge.sol (L283-288)
```text
    modifier onlySupportedChain(uint8 targetChainID) {
        require(
            committee.config().isChainSupported(targetChainID),
            "SuiBridge: Target chain not supported"
        );
        _;
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

**File:** crates/sui-framework/packages/bridge/sources/bridge.move (L57-57)
```text
    sequence_nums: VecMap<u8, u64>,
```

**File:** crates/sui-framework/packages/bridge/sources/bridge.move (L521-535)
```text
// Claim token from approved bridge message
// Returns Some(Coin) if coin can be claimed. If already claimed, return None
fun claim_token_internal<T>(
    bridge: &mut Bridge,
    clock: &Clock,
    source_chain: u8,
    bridge_seq_num: u64,
    ctx: &mut TxContext,
): (Option<Coin<T>>, address) {
    let inner = load_inner_mut(bridge);
    assert!(!inner.paused, EBridgeUnavailable);

    let key = message::create_key(source_chain, message_types::token(), bridge_seq_num);

    assert!(inner.token_transfer_records.contains(key), EMessageNotFoundInRecords);
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
