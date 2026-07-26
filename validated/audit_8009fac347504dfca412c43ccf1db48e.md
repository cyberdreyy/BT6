### Title
Decimal Truncation in `convertERC20ToSuiDecimal` Permanently Locks Sub-Precision ETH in `BridgeVault` - (`bridge/evm/contracts/utils/BridgeUtils.sol`)

---

### Summary

`BridgeUtils.convertERC20ToSuiDecimal` uses integer division to scale ERC20 amounts down to Sui's decimal precision. The truncated remainder is never refunded and has no recovery path in `BridgeVault`, so any ETH deposited via `bridgeERC20` / `bridgeETH` (and their V2 variants) whose amount is not an exact multiple of the scaling factor is permanently locked in the vault with no corresponding claim on Sui.

---

### Finding Description

`convertERC20ToSuiDecimal` in `bridge/evm/contracts/utils/BridgeUtils.sol` performs:

```solidity
uint256 factor = 10 ** (erc20Decimal - suiDecimal);
amount = amount / factor;          // integer division — remainder silently dropped
``` [1](#0-0) 

For ETH (18 ERC20 decimals → 8 Sui decimals), `factor = 10^10`. Any wei amount that is not a multiple of `10^10` loses the remainder.

In `SuiBridge.bridgeERC20` / `bridgeETH`, the **full** `amountTransfered` is transferred to the vault:

```solidity
SafeERC20.safeTransferFrom(IERC20(tokenAddress), msg.sender, address(vault), amount);
``` [2](#0-1) 

But only the truncated `suiAdjustedAmount` is emitted in `TokensDeposited`:

```solidity
uint64 suiAdjustedAmount = BridgeUtils.convertERC20ToSuiDecimal(
    IERC20Metadata(tokenAddress).decimals(),
    config.tokenSuiDecimalOf(tokenID),
    amountTransfered
);
emit TokensDeposited(... suiAdjustedAmount ...);
``` [3](#0-2) 

The bridge node reads `suiAdjustedAmount` from the event and mints exactly that amount on Sui. The difference `amountTransfered − suiAdjustedAmount × factor` stays in the vault forever. The same pattern is present in `SuiBridgeV2.bridgeERC20V2` and `bridgeETHV2`: [4](#0-3) 

`BridgeVault` exposes only `transferERC20` / `transferETH`, both `onlyOwner` (the bridge contract). The bridge contract has no admin-sweep or dust-recovery function, so the truncated wei is irrecoverable: [5](#0-4) 

---

### Impact Explanation

The broken invariant is:

> `vault_balance_increase == tokens_credited_on_Sui`

For every ETH bridge call where `amountTransfered mod 10^10 ≠ 0`, the vault receives more than Sui credits. The remainder (up to `10^10 − 1 = 9,999,999,999 wei ≈ 0.00000001 ETH` per transaction) is permanently locked with no claim path. This is a **permanent fund lock** reachable by any unprivileged bridge user. The per-transaction loss is small but accumulates across all ETH bridge calls and has no recovery mechanism.

Only ETH is affected (18 ERC20 decimals vs. 8 Sui decimals). USDC, USDT, and BTC all have equal ERC20 and Sui decimal counts, so `factor = 1` and no truncation occurs. [6](#0-5) 

---

### Likelihood Explanation

Any ordinary user bridging ETH with a non-round amount (i.e., any amount not a multiple of `10^10 wei`) triggers the lock. This is the common case — wallets and dApps routinely produce amounts like `1.5 ETH` or `0.123456789012345678 ETH` that are not multiples of `10^10 wei`. No special privilege or knowledge is required.

---

### Recommendation

Before transferring tokens to the vault, compute the exact amount that will be credited on Sui and transfer only that amount, refunding the remainder to the caller:

```solidity
uint256 factor = 10 ** (erc20Decimal - suiDecimal);
uint256 alignedAmount = (amountTransfered / factor) * factor;
uint256 dust = amountTransfered - alignedAmount;
// transfer only alignedAmount to vault; refund dust to msg.sender
```

Alternatively, revert if `amountTransfered % factor != 0`, forcing callers to supply precision-aligned amounts.

---

### Proof of Concept

1. User calls `SuiBridge.bridgeERC20(ETH_TOKEN_ID, 1_000_000_000_000_000_001, recipientAddress, destinationChainID)` — bridging `1 ETH + 1 wei`.
2. `SafeERC20.safeTransferFrom` moves `1_000_000_000_000_000_001 wei` into the vault.
3. `convertERC20ToSuiDecimal(18, 8, 1_000_000_000_000_000_001)`:
   - `factor = 10^10`
   - `amount = 1_000_000_000_000_000_001 / 10_000_000_000 = 100_000_000` (truncated)
4. `TokensDeposited` emits `suiAdjustedAmount = 100_000_000` (exactly 1 ETH in Sui units).
5. Bridge node mints `100_000_000` Sui-ETH units for the recipient — equivalent to exactly 1 ETH.
6. The extra `1 wei` remains in the vault with no corresponding Sui claim, permanently locked. [7](#0-6) [8](#0-7)

### Citations

**File:** bridge/evm/contracts/utils/BridgeUtils.sol (L125-151)
```text
    function convertERC20ToSuiDecimal(uint8 erc20Decimal, uint8 suiDecimal, uint256 amount)
        internal
        pure
        returns (uint64)
    {
        if (erc20Decimal == suiDecimal) {
            // ensure provided amount is greater than 0
            require(amount > 0, "BridgeUtils: Insufficient amount provided");
            // Ensure converted amount fits within uint64
            require(amount <= type(uint64).max, "BridgeUtils: Amount too large for uint64");
            return uint64(amount);
        }

        require(erc20Decimal > suiDecimal, "BridgeUtils: Invalid Sui decimal");

        // Difference in decimal places
        uint256 factor = 10 ** (erc20Decimal - suiDecimal);
        amount = amount / factor;

        // Ensure the converted amount fits within uint64
        require(amount <= type(uint64).max, "BridgeUtils: Amount too large for uint64");

        // Ensure the converted amount is greater than 0
        require(amount > 0, "BridgeUtils: Insufficient amount provided");

        return uint64(amount);
    }
```

**File:** bridge/evm/contracts/SuiBridge.sol (L158-188)
```text
        // calculate old vault balance
        uint256 oldBalance = IERC20(tokenAddress).balanceOf(address(vault));

        // Transfer the tokens from the contract to the vault
        SafeERC20.safeTransferFrom(IERC20(tokenAddress), msg.sender, address(vault), amount);

        // calculate new vault balance
        uint256 newBalance = IERC20(tokenAddress).balanceOf(address(vault));

        // calculate the amount transferred
        uint256 amountTransfered = newBalance - oldBalance;

        // Adjust the amount
        uint64 suiAdjustedAmount = BridgeUtils.convertERC20ToSuiDecimal(
            IERC20Metadata(tokenAddress).decimals(),
            config.tokenSuiDecimalOf(tokenID),
            amountTransfered
        );

        emit TokensDeposited(
            config.chainID(),
            nonces[BridgeUtils.TOKEN_TRANSFER],
            destinationChainID,
            tokenID,
            suiAdjustedAmount,
            msg.sender,
            recipientAddress
        );

        // increment token transfer nonce
        nonces[BridgeUtils.TOKEN_TRANSFER]++;
```

**File:** bridge/evm/contracts/SuiBridgeV2.sol (L112-116)
```text
        uint64 suiAdjustedAmount = BridgeUtils.convertERC20ToSuiDecimal(
            IERC20Metadata(tokenAddress).decimals(),
            config.tokenSuiDecimalOf(tokenID),
            amountTransfered
        );
```

**File:** bridge/evm/contracts/BridgeVault.sol (L37-64)
```text
    function transferERC20(address tokenAddress, address recipientAddress, uint256 amount)
        external
        override
        onlyOwner
        nonReentrant
    {
        // Transfer the tokens from the contract to the target address
        SafeERC20.safeTransfer(IERC20(tokenAddress), recipientAddress, amount);
    }

    /// @notice Unwraps stored wrapped ETH and transfers the newly withdrawn ETH to the provided target
    /// address. Only the owner of the contract can call this function.
    /// @dev This function is intended to only be called by the SuiBridge contract.
    /// @param recipientAddress The address to transfer the ETH to.
    /// @param amount The amount of ETH to transfer.
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
