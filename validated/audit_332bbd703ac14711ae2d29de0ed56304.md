### Title
ETH Recipient Can Permanently Lock Bridge Funds by Reverting on ETH Receive — (File: bridge/evm/contracts/SuiBridge.sol)

---

### Summary

`SuiBridge.transferBridgedTokensWithSignatures` performs a raw ETH push to the recipient address **before** marking the bridge message nonce as processed. If the recipient is a contract whose `receive()` or `fallback()` reverts, the entire call reverts, the nonce is never recorded, and the ETH is permanently unclaimable from the vault. The `nonReentrant` guard prevents reentrancy but does not prevent this revert-based denial.

---

### Finding Description

`transferBridgedTokensWithSignatures` follows this sequence:

1. Check nonce not yet processed (line 65)
2. Call `_transferTokensFromVault(...)` → `vault.transferETH(payable(recipientAddress), amount)` (lines 84–89)
3. Set `isTransferProcessed[message.nonce] = true` (line 92) [1](#0-0) 

Inside `BridgeVault.transferETH`, the vault unwraps WETH and sends raw ETH to the recipient with a hard `require`:

```solidity
wETH.withdraw(amount);
(bool success,) = recipientAddress.call{value: amount}("");
require(success, "ETH transfer failed");
``` [2](#0-1) 

If `recipientAddress` is a contract whose `receive()` reverts, `success` is `false`, the `require` propagates the revert up through `_transferTokensFromVault` and out of `transferBridgedTokensWithSignatures`. Because step 3 is never reached, `isTransferProcessed[message.nonce]` remains `false`. Every subsequent retry of the same committee-signed message hits the same revert. The ETH stays in the vault with no recovery path. [3](#0-2) 

The `nonReentrant` modifier (inherited from `ReentrancyGuardUpgradeable` via `CommitteeUpgradeable`) blocks reentrancy into `SuiBridge` functions during the call, but it does not prevent the recipient from simply reverting, which is a distinct and unguarded failure mode. [4](#0-3) 

---

### Impact Explanation

Any ETH bridge message whose EVM recipient address is a contract that reverts on plain ETH receipt (e.g., a multisig wallet, a proxy without a payable fallback, or a deliberately hostile contract) can never be finalized. The ETH is locked in `BridgeVault` indefinitely. The Sui-side sender loses their tokens with no recourse, satisfying the **permanent fund lock** impact class (High/Medium).

---

### Likelihood Explanation

Smart-contract wallets that do not implement a payable `receive()` are common. A Sui-side user who bridges ETH to such an address — or to an address where a hostile party later deploys a reverting contract — triggers the lock without any privileged action. The attacker model is an ordinary EVM address holder; no validator, admin, or governance quorum involvement is required.

---

### Recommendation

Apply the **checks-effects-interactions** pattern: mark `isTransferProcessed[message.nonce] = true` **before** calling `_transferTokensFromVault`. Additionally, replace the ETH push with a **pull-payment** pattern: credit the recipient's claimable balance in a mapping and let them withdraw in a separate transaction. This eliminates the revert-based lock entirely and is the same fix recommended in the source report for `feeTransfer()`.

```solidity
// BEFORE transfer:
isTransferProcessed[message.nonce] = true;

// Store for pull-payment instead of push:
pendingETH[recipientAddress] += amount;
```

---

### Proof of Concept

1. Alice bridges 1 ETH from Sui, specifying Bob's EVM address as recipient.
2. Bob's address is a contract with `receive() external payable { revert(); }`.
3. The committee signs the bridge message; anyone calls `transferBridgedTokensWithSignatures`.
4. Execution reaches `BridgeVault.transferETH` → `recipientAddress.call{value: 1 ether}("")` → Bob's contract reverts → `success = false` → `require(success, "ETH transfer failed")` reverts.
5. `isTransferProcessed[nonce]` is never set. Every retry of the same signed message produces the same revert.
6. The 1 ETH remains in `BridgeVault` with no mechanism to redirect or recover it. [5](#0-4) [6](#0-5)

### Citations

**File:** bridge/evm/contracts/SuiBridge.sol (L64-92)
```text
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

**File:** bridge/evm/contracts/utils/CommitteeUpgradeable.sol (L15-18)
```text
abstract contract CommitteeUpgradeable is
    UUPSUpgradeable,
    MessageVerifier,
    ReentrancyGuardUpgradeable
```
