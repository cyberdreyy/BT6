### Title
Unbounded ETH Push to Malicious Recipient Permanently Locks Bridge Funds — (`bridge/evm/contracts/BridgeVault.sol`)

---

### Summary

`BridgeVault.transferETH` forwards native ETH to an arbitrary recipient address via an unbounded `call{value: amount}("")` with no gas stipend. A malicious recipient contract can implement a gas-exhausting `receive()` function, causing every claim attempt to revert. Because `isTransferProcessed[message.nonce]` is written **after** the vault call in both `SuiBridge.transferBridgedTokensWithSignatures` and `SuiBridgeV2.transferBridgedTokensWithSignaturesV2`, the nonce is never marked processed and the ETH is permanently locked in the vault.

---

### Finding Description

`BridgeVault.transferETH` unwraps WETH and pushes raw ETH to the caller-supplied `recipientAddress` with no gas cap:

```solidity
// bridge/evm/contracts/BridgeVault.sol:52-64
function transferETH(address payable recipientAddress, uint256 amount)
    external override onlyOwner nonReentrant
{
    wETH.withdraw(amount);
    (bool success,) = recipientAddress.call{value: amount}("");   // ← no gas limit
    require(success, "ETH transfer failed");
}
```

This is invoked from `SuiBridge._transferTokensFromVault` whenever `tokenID == BridgeUtils.ETH`:

```solidity
// bridge/evm/contracts/SuiBridge.sol:256-257
if (tokenID == BridgeUtils.ETH) {
    vault.transferETH(payable(recipientAddress), amount);
```

The public entry point `transferBridgedTokensWithSignatures` (and its V2 counterpart) writes the processed flag **only after** the vault call returns successfully:

```solidity
// bridge/evm/contracts/SuiBridge.sol:84-92
_transferTokensFromVault(...);          // reverts if recipient griefs gas
isTransferProcessed[message.nonce] = true;   // never reached on revert
```

Because the nonce is never marked, every subsequent retry of the same signed message also reverts, making the ETH irrecoverable without a governance upgrade.

**Attacker steps (ordinary bridge user, no privilege required):**

1. Deploy a malicious contract on Ethereum whose `receive()` burns all forwarded gas (e.g., an infinite loop or a large storage write).
2. On Sui, call `bridge::send_token` (or `send_token_v2`) specifying the malicious contract address as the EVM recipient — a normal, permissionless user action.
3. The bridge committee signs the resulting `BridgeMessage` (routine operation).
4. Anyone submits the signed message to `transferBridgedTokensWithSignatures`.
5. `vault.transferETH` calls the malicious `receive()` with all remaining gas; the call reverts.
6. `require(success, "ETH transfer failed")` propagates the revert.
7. `isTransferProcessed[message.nonce]` is never set; the ETH stays locked in the vault forever.

The same path exists in `SuiBridgeV2.transferBridgedTokensWithSignaturesV2` → `_transferTokensFromVault` (V2 overload) → `vault.transferETH`.

---

### Impact Explanation

The bridged ETH is held in `BridgeVault`. Once the signed message is created with a malicious recipient, no on-chain mechanism can redirect the funds to a different address or mark the transfer as processed. The ETH is permanently locked — matching the **permanent fund lock** impact class.

---

### Likelihood Explanation

Any Sui user who holds bridgeable ETH can execute this attack. The only prerequisite is deploying a cheap malicious contract on Ethereum and initiating a standard bridge transfer. No committee collusion, no validator access, and no special privilege is required. The attack is repeatable for each new nonce.

---

### Recommendation

1. **Cap the gas forwarded to the recipient** — use `call{value: amount, gas: 2300}("")` (the EIP-1884-safe stipend) so that only simple EOA receives succeed.
2. **Prefer a pull-payment pattern** — store the owed ETH in a mapping and let recipients call a `withdraw()` function, eliminating the push entirely.
3. **Mark the nonce before the external call** — set `isTransferProcessed[message.nonce] = true` before calling `_transferTokensFromVault` (checks-effects-interactions), so a revert in the vault does not leave the nonce in a permanently retryable-but-always-failing state.

---

### Proof of Concept

```solidity
// Malicious recipient deployed on Ethereum
contract GasGriefRecipient {
    receive() external payable {
        // Exhaust all forwarded gas
        uint256 i;
        while (true) { i++; }
    }
}
```

1. Deploy `GasGriefRecipient` on Ethereum; record its address `0xGRIEF`.
2. On Sui, call `bridge::send_token<ETH>(target_chain=ETH, target_address=0xGRIEF, coin)`.
3. Bridge committee signs the resulting `BridgeMessage` with nonce `N`.
4. Call `SuiBridge.transferBridgedTokensWithSignatures(signatures, message)`.
5. Execution path: `_transferTokensFromVault` → `vault.transferETH(0xGRIEF, amount)` → `0xGRIEF.call{value:amount}("")` → out-of-gas → revert.
6. Observe: `isTransferProcessed[N]` remains `false`; vault WETH balance unchanged; ETH permanently locked. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** bridge/evm/contracts/BridgeVault.sol (L52-64)
```text
    function transferETH(address payable recipientAddress, uint256 amount)
        external
        override
        onlyOwner
        nonReentrant
    {
        // Unwrap the WETH
        wETH.withdraw(amount);

        // Transfer the unwrapped ETH to the target address
        (bool success,) = recipientAddress.call{value: amount}("");
        require(success, "ETH transfer failed");
    }
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

**File:** bridge/evm/contracts/SuiBridge.sol (L244-265)
```text
    function _transferTokensFromVault(
        uint8 sendingChainID,
        uint8 tokenID,
        address recipientAddress,
        uint256 amount
    ) private whenNotPaused limitNotExceeded(sendingChainID, tokenID, amount) {
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
    }
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

**File:** bridge/evm/contracts/SuiBridgeV2.sol (L187-206)
```text
    function _transferTokensFromVault(
        uint8 sendingChainID,
        uint8 tokenID,
        address recipientAddress,
        uint256 amount,
        uint256 timestampSeconds
    ) private whenNotPaused limitNotExceededV2(sendingChainID, tokenID, amount, timestampSeconds) {
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
    }
```
