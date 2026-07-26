### Title
ETH Permanently Locked in BridgeVault When Recipient Contract Lacks Payable Fallback — (`File: bridge/evm/contracts/BridgeVault.sol`)

### Summary

`BridgeVault.transferETH` unwraps WETH and delivers raw ETH to the recipient via a low-level `call{value: amount}("")`. If the recipient is a smart contract without a `payable fallback` or `receive()` function, the call returns `success = false`, the `require` reverts the entire `transferBridgedTokensWithSignatures` transaction, `isTransferProcessed[nonce]` is never set, and the ETH is permanently stranded in the vault. Because the corresponding Sui-side tokens were already burned by `send_token_internal` before any EVM action occurs, the user suffers a permanent double loss: burned Sui assets and locked EVM ETH.

### Finding Description

**Outbound flow (Sui → EVM):**

1. User calls `bridge::send_token<T>` on Sui. `send_token_internal` immediately burns the coin via `inner.treasury.burn(token)` and stores a `BridgeRecord` with `verified_signatures: none()` and `claimed: false`. The tokens are gone from the user's wallet at this point.

2. Bridge authorities collect signatures and call `SuiBridge.transferBridgedTokensWithSignatures` on EVM. This calls `_transferTokensFromVault`, which for `tokenID == BridgeUtils.ETH` calls `vault.transferETH(payable(recipientAddress), amount)`.

3. Inside `BridgeVault.transferETH`:

```solidity
wETH.withdraw(amount);                                    // unwrap WETH → ETH
(bool success,) = recipientAddress.call{value: amount}(""); // low-level ETH push
require(success, "ETH transfer failed");                  // reverts if recipient rejects
```

4. If `recipientAddress` is a contract without a `payable fallback` or `receive()`, the call returns `false`, the `require` reverts the whole transaction, and `isTransferProcessed[message.nonce]` is **never written**. Every subsequent retry by bridge authorities produces the same revert. The ETH stays in the vault indefinitely.

**Root cause:** The vault unconditionally unwraps WETH to raw ETH and pushes it with a bare `call`, mirroring the exact pattern in Scroll's `dropMessage` → `onDropMessage` callback failure. There is no fallback to transfer WETH instead, no mechanism to redirect to a different address, and no on-chain refund path back to the Sui sender.

### Impact Explanation

- **Permanent fund lock (ETH side):** ETH is trapped in `BridgeVault` with no recovery path. The nonce is never marked processed, so the message cannot be retired without a governance upgrade.
- **Permanent burn (Sui side):** The bridged token (e.g., wrapped ETH) was already burned on Sui before the EVM transaction was attempted. The user loses both the Sui asset and the EVM ETH.
- Matches the allowed impact gate: *permanent fund lock* and *unintended permanent burning below the 10B cap*.

### Likelihood Explanation

Any ordinary bridge user who specifies an EVM recipient that is a contract without a payable fallback triggers this. Common real-world examples include multisig wallets (Gnosis Safe without ETH receive), DAO treasuries, and protocol contracts that only accept ERC-20 tokens. No special privilege is required; the attacker model is an ordinary bridge user.

### Recommendation

Replace the raw-ETH push with a WETH transfer so the recipient can always receive value regardless of its fallback implementation:

```solidity
// Instead of unwrapping and pushing raw ETH:
// wETH.withdraw(amount);
// (bool success,) = recipientAddress.call{value: amount}("");
// require(success, "ETH transfer failed");

// Transfer WETH directly — always succeeds for any address:
SafeERC20.safeTransfer(IERC20(address(wETH)), recipientAddress, amount);
```

Alternatively, attempt the raw-ETH transfer and fall back to WETH on failure, or add an on-chain refund path that allows the Sui-side sender to reclaim their burned tokens when the EVM delivery is provably impossible.

### Proof of Concept

1. Deploy a no-fallback contract on EVM:
```solidity
contract NoReceive { /* no receive() or fallback() */ }
```

2. On Sui, call `bridge::send_token<ETH>(bridge, ETH_CHAIN_ID, noReceive.address_as_bytes, eth_coin, ctx)`. The wETH coin is burned immediately.

3. Bridge authorities collect quorum signatures and submit `transferBridgedTokensWithSignatures` on EVM. `_transferTokensFromVault` calls `vault.transferETH(payable(noReceive), amount)`.

4. `wETH.withdraw(amount)` succeeds; `noReceive.call{value: amount}("")` returns `(false, "")` because `NoReceive` has no payable entry point; `require(success, "ETH transfer failed")` reverts the transaction.

5. `isTransferProcessed[nonce]` remains `false`. Every retry reverts identically. The ETH is permanently locked in `BridgeVault`. The Sui-side wETH is permanently burned. No recovery path exists without a governance-controlled contract upgrade.

---

**Relevant code locations:**

`BridgeVault.transferETH` — the raw ETH push that fails for non-payable recipients: [1](#0-0) 

`SuiBridge._transferTokensFromVault` — calls `vault.transferETH` for ETH token type: [2](#0-1) 

`bridge::send_token_internal` — burns Sui-side tokens before any EVM action: [3](#0-2) 

`SuiBridge.transferBridgedTokensWithSignatures` — marks nonce processed only after vault transfer succeeds: [4](#0-3)

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

**File:** bridge/evm/contracts/SuiBridge.sol (L61-103)
```text
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

**File:** crates/sui-framework/packages/bridge/sources/bridge.move (L610-633)
```text
fun send_token_internal<T>(
    inner: &mut BridgeInner,
    target_chain: u8,
    token: Coin<T>,
    message: BridgeMessage,
) {
    assert!(!inner.paused, EBridgeUnavailable);
    assert!(chain_ids::is_valid_route(inner.chain_id, target_chain), EInvalidBridgeRoute);

    // burn / escrow token, unsupported coins will fail in this step
    inner.treasury.burn(token);

    // Store pending bridge request
    inner
        .token_transfer_records
        .push_back(
            message.key(),
            BridgeRecord {
                message,
                verified_signatures: option::none(),
                claimed: false,
            },
        );
}
```
