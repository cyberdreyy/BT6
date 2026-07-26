The vulnerability is clearly present. Let me verify the exact execution order in `transferBridgedTokensWithSignatures` to confirm the permanent lock condition.

I have all the information needed. The vulnerability is confirmed and fully traceable.

---

### Title
ETH Recipient Revert Permanently Locks WETH in BridgeVault — (`bridge/evm/contracts/BridgeVault.sol`)

### Summary

`BridgeVault.transferETH` unwraps WETH and pushes raw native ETH to an untrusted, user-supplied `recipientAddress` via a low-level `.call`. If that address is a contract whose `receive()` reverts, the call returns `success = false`, the `require` reverts the entire transaction, and — because `isTransferProcessed[nonce]` is written **after** the vault call — the nonce is never consumed. Every future attempt to settle the same bridge message also reverts. The corresponding WETH is permanently locked in the vault with no on-chain recovery path.

### Finding Description

`BridgeVault.transferETH` performs two sequential operations:

```solidity
// bridge/evm/contracts/BridgeVault.sol  lines 58-63
wETH.withdraw(amount);                                    // unwrap WETH → raw ETH
(bool success,) = recipientAddress.call{value: amount}(""); // push to recipient
require(success, "ETH transfer failed");                  // revert if rejected
``` [1](#0-0) 

This is called from `SuiBridge._transferTokensFromVault` whenever `tokenID == BridgeUtils.ETH`:

```solidity
// bridge/evm/contracts/SuiBridge.sol  lines 256-257
if (tokenID == BridgeUtils.ETH) {
    vault.transferETH(payable(recipientAddress), amount);
``` [2](#0-1) 

The critical ordering in `transferBridgedTokensWithSignatures` is:

```
line 84  → _transferTokensFromVault(...)   // vault call — can revert
line 92  → isTransferProcessed[nonce] = true  // only reached on success
``` [3](#0-2) 

Because the nonce-mark is **after** the vault call, a revert inside `transferETH` rolls back the entire transaction and leaves `isTransferProcessed[nonce]` as `false`. The identical pattern exists in `SuiBridgeV2.transferBridgedTokensWithSignaturesV2`: [4](#0-3) 

The Ethereum `recipientAddress` is fully user-controlled: it is embedded in the bridge message payload on the Sui side (`eth_address` field in `SuiToEthOnChainBcsPayload`) and faithfully decoded by `BridgeUtils.decodeTokenTransferPayload` without any validation that the address can accept native ETH. [5](#0-4) 

There is no recovery function in `BridgeVault` or `SuiBridge` that could re-route a stuck transfer to a different address or refund the WETH.

### Impact Explanation

A user who initiates a Sui→Ethereum ETH bridge transfer and specifies a contract address that reverts on ETH receipt as the Ethereum recipient causes the corresponding WETH to be permanently locked in `BridgeVault`. The bridge message nonce is never consumed, so the transfer can never be settled. The WETH balance in the vault grows by the locked amount and is irrecoverable without a contract upgrade. This is a **permanent fund lock** matching the High/Medium impact class in the bounty gate.

### Likelihood Explanation

The trigger requires only an ordinary bridge user action: initiating a Sui→ETH transfer with a self-deployed contract (zero `receive()` or explicit `revert()`) as the Ethereum recipient. No privileged role, no committee cooperation, and no special timing is needed. The attacker bears the cost of the locked funds themselves, making this a credible griefing or accidental-loss scenario. Both V1 (`SuiBridge`) and V2 (`SuiBridgeV2`) are affected.

### Recommendation

Replace the native-ETH push in `BridgeVault.transferETH` with a WETH ERC-20 transfer, mirroring the Gearbox fix. WETH is already held in the vault; transferring it as an ERC-20 token cannot be rejected by the recipient's `receive()` hook:

```solidity
// Recommended replacement for BridgeVault.transferETH
function transferETH(address recipientAddress, uint256 amount)
    external override onlyOwner nonReentrant
{
    // Transfer WETH directly — no unwrap, no ETH push, no revert risk
    SafeERC20.safeTransfer(IERC20(address(wETH)), recipientAddress, amount);
}
```

Alternatively, if native ETH delivery is required, use a pull-payment pattern: credit the recipient's claimable balance and let them withdraw, so a reverting `receive()` cannot block settlement.

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract RevertOnETH {
    // No receive() — any ETH push reverts automatically
}

// Attack steps (Foundry pseudocode):
// 1. Deploy RevertOnETH on Ethereum.
// 2. On Sui, call the bridge's send_token entry with:
//      token  = ETH coin
//      target = Ethereum chain
//      recipient = address(RevertOnETH)
// 3. Bridge committee observes the Sui event, signs the message,
//    and calls transferBridgedTokensWithSignatures / V2 on Ethereum.
// 4. Execution path:
//      transferBridgedTokensWithSignatures
//        → _transferTokensFromVault (tokenID == ETH)
//          → vault.transferETH(payable(address(RevertOnETH)), amount)
//            → wETH.withdraw(amount)          // succeeds
//            → address(RevertOnETH).call{value}("") // REVERTS (no receive)
//            → require(false) → revert "ETH transfer failed"
//      isTransferProcessed[nonce] never written
// 5. Every retry of the same signed message reverts identically.
// 6. The WETH remains in BridgeVault with no recovery path.
``` [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** bridge/evm/contracts/SuiBridge.sol (L84-92)
```text
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

**File:** bridge/evm/contracts/SuiBridgeV2.sol (L46-55)
```text
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

**File:** bridge/evm/contracts/utils/BridgeUtils.sol (L186-233)
```text
    function decodeTokenTransferPayload(bytes memory _payload)
        internal
        pure
        returns (TokenTransferPayload memory)
    {
        require(_payload.length == 64, "BridgeUtils: TokenTransferPayload must be 64 bytes");

        uint8 senderAddressLength = uint8(_payload[0]);

        require(
            senderAddressLength == 32,
            "BridgeUtils: Invalid sender address length, Sui address must be 32 bytes"
        );

        // used to offset already read bytes
        uint8 offset = 1;

        // extract sender address from payload bytes 1-32
        bytes memory senderAddress = new bytes(senderAddressLength);
        for (uint256 i; i < senderAddressLength; i++) {
            senderAddress[i] = _payload[i + offset];
        }

        // move offset past the sender address length
        offset += senderAddressLength;

        // target chain is a single byte
        uint8 targetChain = uint8(_payload[offset++]);

        // target address length is a single byte
        uint8 recipientAddressLength = uint8(_payload[offset++]);
        require(
            recipientAddressLength == 20,
            "BridgeUtils: Invalid target address length, EVM address must be 20 bytes"
        );

        // extract target address from payload (35-54)
        address recipientAddress;

        // why `add(recipientAddressLength, offset)`?
        // At this point, offset = 35, recipientAddressLength = 20. `mload(add(payload, 55))`
        // reads the next 32 bytes from bytes 23 in paylod, because the first 32 bytes
        // of payload stores its length. So in reality, bytes 23 - 54 is loaded. During
        // casting to address (20 bytes), the least sigificiant bytes are retained, namely
        // `recipientAddress` is bytes 35-54
        assembly {
            recipientAddress := mload(add(_payload, add(recipientAddressLength, offset)))
        }
```
