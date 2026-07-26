### Title
ETH Decimal-Truncation Dust Permanently Locked in BridgeVault on Every `bridgeETH` / `bridgeETHV2` Call — (`bridge/evm/contracts/SuiBridge.sol`, `bridge/evm/contracts/SuiBridgeV2.sol`)

---

### Summary

`bridgeETH` and `bridgeETHV2` forward the caller's entire `msg.value` to `BridgeVault`, then call `BridgeUtils.convertERC20ToSuiDecimal(18, 8, msg.value)` which performs integer division by `10^10`. The remainder (`msg.value % 10^10`, up to 9,999,999,999 wei ≈ 9.99 Gwei per call) is silently discarded from the cross-chain message but remains locked in the vault. Because the vault has no sweep or recovery path accessible without a new committee-signed bridge message, this dust is permanently irrecoverable. The same truncation applies to `bridgeERC20` / `bridgeERC20V2` for any ERC-20 token whose EVM decimal count exceeds its Sui decimal count.

---

### Finding Description

**Root cause — integer truncation without refund:**

`convertERC20ToSuiDecimal` divides by `factor = 10 ** (erc20Decimal - suiDecimal)`:

```solidity
// BridgeUtils.sol L141-142
uint256 factor = 10 ** (erc20Decimal - suiDecimal);
amount = amount / factor;          // truncates; remainder is silently dropped
``` [1](#0-0) 

For ETH: `erc20Decimal = 18`, `suiDecimal = 8`, so `factor = 10^10`.

**Deposit path — full `msg.value` enters the vault:**

```solidity
// SuiBridge.sol L207-221
uint256 amount = msg.value;
(bool success,) = payable(address(vault)).call{value: amount}("");   // ALL wei locked
...
uint64 suiAdjustedAmount = BridgeUtils.convertERC20ToSuiDecimal(
    IERC20Metadata(config.tokenAddressOf(BridgeUtils.ETH)).decimals(),
    config.tokenSuiDecimalOf(BridgeUtils.ETH),
    amount                                                            // truncated here
);
emit TokensDeposited(..., suiAdjustedAmount, ...);                   // only truncated amount bridged
``` [2](#0-1) 

The bridge node reads `suiAdjustedAmount` from the event and mints exactly that many tokens on Sui. The remainder `amount % 10^10` is never referenced again.

The identical pattern exists in `SuiBridgeV2.bridgeETHV2`: [3](#0-2) 

**No vault recovery path:**

`BridgeVault.transferETH` is `onlyOwner` (owner = SuiBridge), and SuiBridge only calls it via `_transferTokensFromVault` when processing a committee-signed message. There is no admin sweep, no dust-recovery function, and no fallback: [4](#0-3) 

---

### Impact Explanation

Every ETH bridge transaction where `msg.value % 10^10 ≠ 0` permanently locks up to 9,999,999,999 wei in the vault. The user receives fewer bridged tokens than they paid for, and the difference is irrecoverable without a full contract upgrade requiring committee governance. This matches the **permanent fund lock** impact class in the Sui bounty scope (High/Medium).

---

### Likelihood Explanation

Any ordinary user calling `bridgeETH{value: X}(...)` where `X` is not an exact multiple of 10 Gwei triggers the loss. Wallets and dApps routinely produce non-round wei amounts (e.g., 1.5 ETH = 1,500,000,000,000,000,000 wei, which is divisible; but 1.123456789012345 ETH = 1,123,456,789,012,345,000 wei → remainder 9,012,345,000 wei ≈ 9 Gwei locked). No special privilege is required; the trigger is a standard public payable call.

---

### Recommendation

Before forwarding ETH to the vault, compute the truncated amount first and refund the remainder to `msg.sender`:

```solidity
function bridgeETH(bytes memory recipientAddress, uint8 destinationChainID)
    external payable whenNotPaused nonReentrant onlySupportedChain(destinationChainID)
{
    IBridgeConfig config = committee.config();
    uint8 ethErc20Dec = IERC20Metadata(config.tokenAddressOf(BridgeUtils.ETH)).decimals();
    uint8 ethSuiDec   = config.tokenSuiDecimalOf(BridgeUtils.ETH);

    uint64 suiAdjustedAmount = BridgeUtils.convertERC20ToSuiDecimal(
        ethErc20Dec, ethSuiDec, msg.value
    );
    uint256 factor        = 10 ** (ethErc20Dec - ethSuiDec);
    uint256 acceptedWei   = uint256(suiAdjustedAmount) * factor;   // exact, no dust
    uint256 dustWei       = msg.value - acceptedWei;

    (bool ok,) = payable(address(vault)).call{value: acceptedWei}("");
    require(ok, "SuiBridge: Failed to transfer ETH to vault");

    if (dustWei > 0) {
        (bool refunded,) = payable(msg.sender).call{value: dustWei}("");
        require(refunded, "SuiBridge: Dust refund failed");
    }
    // ... emit, nonce++
}
```

Alternatively, revert if `msg.value % factor != 0`, forcing callers to supply exact multiples of 10 Gwei.

Apply the same fix to `bridgeETHV2`, `bridgeERC20`, and `bridgeERC20V2`.

---

### Proof of Concept

```solidity
// Foundry test — add to bridge/evm/test/SuiBridgeTest.t.sol
function testETHDustLockedInVault() public {
    // 1 ETH + 9 Gwei dust (not a multiple of 10^10)
    uint256 sendAmount = 1 ether + 9_000_000_000;   // 1_000_000_009_000_000_000 wei
    uint256 factor     = 1e10;                       // 18 - 8 decimals
    uint256 dust       = sendAmount % factor;        // 9_000_000_000 wei

    vm.deal(deployer, sendAmount);
    uint256 vaultBefore = address(vault).balance;

    bridge.bridgeETH{value: sendAmount}(
        hex"06bb77410cd326430fa2036c8282dbb54a6f8640cea16ef5eff32d638718b3e4", 0
    );

    uint256 vaultAfter = address(vault).balance;
    // Vault holds the full sendAmount (as WETH after receive())
    // but the event only records sendAmount / 1e10 = 100_000_000 Sui-units (= 1 ETH)
    // The 9 Gwei dust is permanently locked — no recovery path exists
    assertEq(IERC20(wETH).balanceOf(address(vault)), sendAmount);
    assertEq(dust, 9_000_000_000);   // 9 Gwei permanently locked per tx
}
``` [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** bridge/evm/contracts/SuiBridge.sol (L195-235)
```text
    function bridgeETH(bytes memory recipientAddress, uint8 destinationChainID)
        external
        payable
        whenNotPaused
        nonReentrant
        onlySupportedChain(destinationChainID)
    {
        require(
            recipientAddress.length == SUI_ADDRESS_LENGTH,
            "SuiBridge: Invalid recipient address length"
        );

        uint256 amount = msg.value;

        // Transfer the unwrapped ETH to the target address
        (bool success,) = payable(address(vault)).call{value: amount}("");
        require(success, "SuiBridge: Failed to transfer ETH to vault");

        // Adjust the amount to emit.
        IBridgeConfig config = committee.config();

        // Adjust the amount
        uint64 suiAdjustedAmount = BridgeUtils.convertERC20ToSuiDecimal(
            IERC20Metadata(config.tokenAddressOf(BridgeUtils.ETH)).decimals(),
            config.tokenSuiDecimalOf(BridgeUtils.ETH),
            amount
        );

        emit TokensDeposited(
            config.chainID(),
            nonces[BridgeUtils.TOKEN_TRANSFER],
            destinationChainID,
            BridgeUtils.ETH,
            suiAdjustedAmount,
            msg.sender,
            recipientAddress
        );

        // increment token transfer nonce
        nonces[BridgeUtils.TOKEN_TRANSFER]++;
    }
```

**File:** bridge/evm/contracts/SuiBridgeV2.sol (L137-178)
```text
    function bridgeETHV2(bytes memory recipientAddress, uint8 destinationChainID)
        external
        payable
        whenNotPaused
        nonReentrant
        onlySupportedChain(destinationChainID)
    {
        require(
            recipientAddress.length == SUI_ADDRESS_LENGTH,
            "SuiBridge: Invalid recipient address length"
        );

        uint256 amount = msg.value;

        // Transfer the unwrapped ETH to the target address
        (bool success,) = payable(address(vault)).call{value: amount}("");
        require(success, "SuiBridge: Failed to transfer ETH to vault");

        // Adjust the amount to emit.
        IBridgeConfig config = committee.config();

        // Adjust the amount
        uint64 suiAdjustedAmount = BridgeUtils.convertERC20ToSuiDecimal(
            IERC20Metadata(config.tokenAddressOf(BridgeUtils.ETH)).decimals(),
            config.tokenSuiDecimalOf(BridgeUtils.ETH),
            amount
        );

        emit TokensDepositedV2(
            config.chainID(),
            nonces[BridgeUtils.TOKEN_TRANSFER],
            destinationChainID,
            BridgeUtils.ETH,
            suiAdjustedAmount,
            msg.sender,
            recipientAddress,
            block.timestamp
        );

        // increment token transfer nonce
        nonces[BridgeUtils.TOKEN_TRANSFER]++;
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

**File:** bridge/evm/contracts/BridgeVault.sol (L66-72)
```text
    /// @notice Wraps as eth sent to this contract.
    /// @dev skip if sender is wETH contract to avoid infinite loop.
    receive() external payable {
        if (msg.sender != address(wETH)) {
            wETH.deposit{value: msg.value}();
        }
    }
```
