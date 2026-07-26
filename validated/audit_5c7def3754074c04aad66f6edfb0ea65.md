### Title
`BridgeVault.transferETH` Permanently Locks Bridged ETH When Recipient Is a Non-Payable Contract — (File: `bridge/evm/contracts/BridgeVault.sol`)

---

### Summary

`BridgeVault.transferETH` delivers unwrapped ETH to the recipient via a raw `.call{value: amount}("")`. If the recipient is a contract that lacks a `receive` or `fallback` function (or one that reverts on receipt), the call fails, `require(success, "ETH transfer failed")` reverts the entire transaction, and the nonce in `SuiBridge` is never consumed. Because the recipient address is immutably encoded in the signed bridge message and there is no alternative delivery path, the bridged ETH is permanently locked in the vault.

---

### Finding Description

`BridgeVault.transferETH` first unwraps WETH to native ETH, then pushes it to the recipient with a raw call:

```solidity
// BridgeVault.sol lines 59-63
wETH.withdraw(amount);
(bool success,) = recipientAddress.call{value: amount}("");
require(success, "ETH transfer failed");
``` [1](#0-0) 

This function is invoked by `SuiBridge._transferTokensFromVault` whenever `tokenID == BridgeUtils.ETH`:

```solidity
// SuiBridge.sol lines 256-257
if (tokenID == BridgeUtils.ETH) {
    vault.transferETH(payable(recipientAddress), amount);
``` [2](#0-1) 

`_transferTokensFromVault` is called from `transferBridgedTokensWithSignatures` **before** the nonce is marked consumed:

```solidity
// SuiBridge.sol lines 84-92
_transferTokensFromVault(...);   // ← can revert here
isTransferProcessed[message.nonce] = true;  // ← never reached on revert
``` [3](#0-2) 

Because the entire transaction reverts atomically, `isTransferProcessed[message.nonce]` stays `false`. However, the recipient address is fixed inside the committee-signed `BridgeMessage` payload — there is no on-chain mechanism to redirect the ETH to a different address or to fall back to WETH delivery. Every subsequent retry of `transferBridgedTokensWithSignatures` with the same message will hit the same revert. The identical flaw exists in `SuiBridgeV2._transferTokensFromVault` and `transferBridgedTokensWithSignaturesV2`: [4](#0-3) [5](#0-4) 

---

### Impact Explanation

Any ETH bridged from Sui to an EVM contract address that cannot receive native ETH (e.g., a multisig, DAO treasury, or smart-contract wallet without a `receive`/`fallback`) is permanently locked in `BridgeVault`. The WETH held in the vault cannot be unwrapped and delivered, and no on-chain recovery path exists without a contract upgrade by the committee. This constitutes a **permanent fund lock** matching the allowed High/Medium impact class.

---

### Likelihood Explanation

Contract addresses are common bridge recipients: multisigs (Gnosis Safe), DAO treasuries, and smart-contract wallets are widely used on Ethereum. Many of these do not implement a generic ETH `receive` function. A user only needs to specify such an address as the Sui-side `recipientAddress` when calling `bridge::send_token` (or the EVM-side `bridgeETH`/`bridgeERC20`). No special privilege is required; any ordinary bridge user can trigger this condition, intentionally or accidentally.

---

### Recommendation

In `BridgeVault.transferETH`, attempt the native ETH transfer and, if it fails, fall back to transferring WETH instead of reverting:

```solidity
function transferETH(address payable recipientAddress, uint256 amount)
    external override onlyOwner nonReentrant
{
    wETH.withdraw(amount);
    (bool success,) = recipientAddress.call{value: amount}("");
    if (!success) {
        // Fallback: re-wrap and send WETH so the recipient can unwrap at will
        wETH.deposit{value: amount}();
        SafeERC20.safeTransfer(IERC20(address(wETH)), recipientAddress, amount);
    }
}
```

This mirrors the mitigation recommended in the original M-09 report and ensures the transfer never reverts due to recipient-side ETH rejection.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// A contract with no receive/fallback — cannot accept ETH
contract NoReceive {}

contract PoC is Test {
    // ... standard bridge test setup (committee, vault, bridge) ...

    function testETHLockedForNonPayableRecipient() public {
        // Fund vault with WETH (simulating prior bridge deposits)
        vm.deal(address(this), 1 ether);
        IWETH9(wETH).deposit{value: 1 ether}();
        IERC20(wETH).transfer(address(vault), 1 ether);

        // Deploy a contract that cannot receive ETH
        NoReceive noReceive = new NoReceive();

        // Build a valid committee-signed TOKEN_TRANSFER message
        // targeting noReceive as the EVM recipient for 1 ETH (BridgeUtils.ETH)
        BridgeUtils.Message memory message = _buildSignedEthTransferMessage(
            address(noReceive), 1 ether
        );
        bytes[] memory sigs = _gatherQuorumSignatures(message);

        // transferBridgedTokensWithSignatures reverts — ETH never delivered
        vm.expectRevert("ETH transfer failed");
        bridge.transferBridgedTokensWithSignatures(sigs, message);

        // Nonce is NOT consumed — but every retry also reverts
        assertFalse(bridge.isTransferProcessed(message.nonce));

        // WETH remains locked in vault; noReceive has 0 ETH
        assertEq(IERC20(wETH).balanceOf(address(vault)), 1 ether);
        assertEq(address(noReceive).balance, 0);
    }
}
```

The test demonstrates that:
1. `transferBridgedTokensWithSignatures` reverts with `"ETH transfer failed"` when the recipient cannot accept ETH. [6](#0-5) 
2. `isTransferProcessed[nonce]` remains `false`, so the nonce is not consumed. [7](#0-6) 
3. WETH stays in the vault with no on-chain path to deliver it to the recipient. [8](#0-7)

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

**File:** bridge/evm/contracts/SuiBridge.sol (L255-257)
```text
        // transfer eth if token type is eth
        if (tokenID == BridgeUtils.ETH) {
            vault.transferETH(payable(recipientAddress), amount);
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

**File:** bridge/evm/contracts/SuiBridgeV2.sol (L199-205)
```text
        // transfer eth if token type is eth
        if (tokenID == BridgeUtils.ETH) {
            vault.transferETH(payable(recipientAddress), amount);
        } else {
            // transfer tokens from vault to target address
            vault.transferERC20(tokenAddress, recipientAddress, amount);
        }
```
