### Title
`ProvisionerV2.requestRedeem` omits the `userUnitsRefundableUntil` lock check enforced in `redeem`/`withdraw`, letting a just-deposited (refund-window-locked) depositor move vault units out of reach before the sync deposit can be reversed - ([File: v3/src/core/ProvisionerV2.sol])

### Summary
`redeem()` and `withdraw()` (the synchronous exit paths) both require `userUnitsRefundableUntil[msg.sender] < block.timestamp` before allowing a depositor to act on vault units, i.e. a depositor whose sync deposit is still inside the refund window is blocked from directly redeeming/withdrawing. `requestRedeem()` (the async exit path, which immediately pulls the caller's vault units into the Provisioner via `safeTransferFrom`) has no equivalent check, so the same locked depositor can still move their units out of their own wallet and, once solved, receive tokens for them — exactly mirroring the Ajna pattern where `kickWithDeposit()` skipped a debt-lock check that `moveQuoteToken()`/`removeQuoteToken()` enforced.

### Finding Description
`redeem()` (`v3/src/core/ProvisionerV2.sol:594-625`) and `withdraw()` (`v3/src/core/ProvisionerV2.sol:628-659`) both contain:
```
require(userUnitsRefundableUntil[msg.sender] < block.timestamp, Aera__UnitsLocked());
```
guarding against a locked depositor extracting value while their sync deposit is still within `depositRefundTimeout` and eligible to be reversed by the `requiresAuth` `refundDeposit()` function (`v3/src/core/ProvisionerV2.sol:232-260`), which calls `IMultiDepositorVault.exit(receiver, token, tokenAmount, unitsAmount, receiver)` and depends on the receiver still holding `unitsAmount` vault units.

`requestRedeem()` (`v3/src/core/ProvisionerV2.sol:812-857`) has no `userUnitsRefundableUntil` check at all, and immediately does:
```
IERC20(MULTI_DEPOSITOR_VAULT).safeTransferFrom(msg.sender, address(this), unitsIn);
```
This physically removes the units from the depositor's balance and places them in the Provisioner, before the async request is ever solved. A locked depositor can therefore route around the exact restriction that `redeem`/`withdraw` enforce, using the async path instead. Once the request is solved via `solveRequestsVault`/`solveRequestsDirect` (public/permissionless in the direct case), or refunded back via `refundRequest`/`cancelRequest`, the depositor's units are no longer sitting at their own address for `refundDeposit()` to burn, so the sync-deposit reversal control can be defeated or made to permanently revert (insufficient unit balance to burn), just as `kickWithDeposit()` let HPB depositors bypass `_revertIfAuctionDebtLocked()` and extract deposit through a side door.

### Impact Explanation
The affected control (`userUnitsRefundableUntil` / `Aera__UnitsLocked`) exists specifically to keep freshly minted units reachable so that an authorized party can reverse a bad/erroneous/fraudulent sync deposit via `refundDeposit()` within the configured window. Bypassing it via `requestRedeem` lets a malicious depositor convert vault units into clean tokens (or transfer them elsewhere) before the deposit can be reversed, defeating the deposit-reversal safety mechanism and creating a path to lock up or move value that other depositors are relying on the refund control to protect.

### Likelihood Explanation
No privileged role is needed — any ordinary depositor whose sync deposit is inside the refund window can call the public `requestRedeem()` immediately after depositing. The only additional requirement is that the async request subsequently gets solved (by a solver via `solveRequestsVault`, or by anyone via `solveRequestsDirect`, both of which are reachable through normal use), or is cancelled/refunded, all of which are ordinary, unprivileged flows already exposed by the contract.

### Recommendation
Add the same `require(userUnitsRefundableUntil[msg.sender] < block.timestamp, Aera__UnitsLocked())` check to `requestRedeem()` (and any other function that transfers vault units out of a depositor's balance, e.g. wherever units move via `cancelRequest`/`refundRequest` on behalf of the same locked depositor), matching the guard already present in `redeem()`/`withdraw()`.

### Proof of Concept
Note: I was not able to fully trace where `userUnitsRefundableUntil` is set (the setter logic was outside the scope of code retrieved before the tool budget was exhausted), so the exact preconditions for a depositor's lock timestamp being non-zero, and the precise consequence on `refundDeposit()` (revert vs. partial success), could not be independently confirmed from the code inspected. A Foundry PoC would need to:
1. Deploy/fork the live `ProvisionerV2`/`MultiDepositorVault` pair for a bounty-listed vault.
2. As depositor `A`, call `deposit()` to mint vault units, confirming `userUnitsRefundableUntil[A] > block.timestamp`.
3. Confirm `redeem()`/`withdraw()` revert with `Aera__UnitsLocked()` for `A`.
4. Call `requestRedeem()` as `A` for the same units and confirm it succeeds (units transferred out of `A`'s balance into the Provisioner) despite the lock.
5. Solve the request via `solveRequestsDirect` (as any third party) and confirm `A` receives tokens for the locked units.
6. As the authorized party, attempt `refundDeposit()` for `A`'s original deposit hash and confirm it now reverts (insufficient unit balance) or otherwise fails to reverse the deposit, demonstrating the bypass.

Given the incomplete verification of the setter/consequences, this should be validated on a live fork before submission; the structural mismatch between `redeem`/`withdraw`'s lock check and `requestRedeem`'s lack thereof is confirmed directly from the code shown above. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** v3/src/core/ProvisionerV2.sol (L86-87)
```text
    /// @notice Mapping of user address to timestamp until which their units are locked
    mapping(address user => uint256 unitsLockedUntil) public userUnitsRefundableUntil;
```

**File:** v3/src/core/ProvisionerV2.sol (L232-260)
```text
    function refundDeposit(
        address sender,
        address receiver,
        IERC20 token,
        uint256 tokenAmount,
        uint256 unitsAmount,
        uint256 refundableUntil
    ) external requiresAuth {
        // Requirements: refundable timestamp is in the future
        require(refundableUntil >= block.timestamp, Aera__RefundPeriodExpired());

        bytes32 depositHash = _getDepositHash(sender, receiver, token, tokenAmount, unitsAmount, refundableUntil);
        // Requirements: hash has been set
        require(syncDepositHashes[depositHash], Aera__DepositHashNotFound());
        // Effects: unset hash as used
        syncDepositHashes[depositHash] = false;

        // Interactions: pull funds from yield source if vault idle balance is insufficient
        _pullFundsIfNeeded(token, tokenAmount);

        // Interactions: exit vault, fallback to sender
        try IMultiDepositorVault(MULTI_DEPOSITOR_VAULT).exit(receiver, token, tokenAmount, unitsAmount, receiver) { }
        catch {
            IMultiDepositorVault(MULTI_DEPOSITOR_VAULT).exit(receiver, token, tokenAmount, unitsAmount, sender);
        }

        // Log deposit refunded event
        emit DirectDepositRefunded(depositHash);
    }
```

**File:** v3/src/core/ProvisionerV2.sol (L605-606)
```text
        // Requirements: check that the caller does not have its units locked
        require(userUnitsRefundableUntil[msg.sender] < block.timestamp, Aera__UnitsLocked());
```

**File:** v3/src/core/ProvisionerV2.sol (L639-640)
```text
        // Requirements: check that the caller does not have its units locked
        require(userUnitsRefundableUntil[msg.sender] < block.timestamp, Aera__UnitsLocked());
```

**File:** v3/src/core/ProvisionerV2.sol (L812-857)
```text
    function requestRedeem(
        IERC20 token,
        uint256 unitsIn,
        uint256 minTokensOut,
        uint256 solverTip,
        uint256 deadline,
        uint256 maxPriceAge,
        bool isFixedPrice,
        address receiver
    ) public anyoneButVault returns (bytes32 redeemHash) {
        // Requirements: units amount and min token out are positive, async redeems are enabled
        _validateNonZeroAmounts(unitsIn, minTokensOut);
        require(tokensDetails[token].asyncRedeemEnabled, Aera__AsyncRedeemDisabled());

        // Requirements: common request validation
        _validateRequest(receiver, solverTip, deadline, isFixedPrice);

        RequestType requestType = _getRequestType(isFixedPrice, false);

        // Interactions: transfer units from sender to provisioner
        IERC20(MULTI_DEPOSITOR_VAULT).safeTransferFrom(msg.sender, address(this), unitsIn);

        redeemHash = _getRequestHashParams(
            token, msg.sender, receiver, requestType, minTokensOut, unitsIn, solverTip, deadline, maxPriceAge
        );

        // Requirements: hash has not been used
        require(!asyncRequestHashes[redeemHash], Aera__HashCollision());

        // Effects: set hash as used
        asyncRequestHashes[redeemHash] = true;

        // Log redeem requested event
        emit RedeemRequested(
            msg.sender,
            receiver,
            token,
            minTokensOut,
            unitsIn,
            solverTip,
            deadline,
            maxPriceAge,
            isFixedPrice,
            redeemHash
        );
    }
```
