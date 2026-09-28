### Title
Funded instant-withdraw claims never clear the per-epoch receipt maps, letting an already-paid user claim a second payout after a same-epoch default - (File: contracts/strategies/idle/IdleCreditVault.sol)

### Summary
`requestInstantWithdraw` records a receipt in three places: the aggregate `instantWithdrawsRequests[user]`, the per-epoch `instantWithdrawsRequestsByEpoch[user][epoch]`, and the global `instantWithdrawClaimsByEpoch[epoch]`. The normal claim path `claimInstantWithdrawRequest` only clears the aggregate, mirroring the reported bug where `votedFor` is updated but `votes` is never decremented: the per-epoch counters are only ever decremented inside `_claimDefaultedInstantWithdrawRequest`. If the epoch in which the receipt was created later defaults and is finalized, the stale per-epoch entry is treated as an unpaid defaulted receipt and pays out a second time from `defaultRecoveryReserve`. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
1. On `requestInstantWithdraw`, the vault mints receipt tokens and increments `instantWithdrawsRequests`, `instantWithdrawsRequestsByEpoch[user][currentEpoch]`, `instantWithdrawClaimsByEpoch[currentEpoch]` and `pendingInstantWithdraws`. [4](#0-3) 
2. When the CDO funds the request, `collectInstantWithdrawFunds` decrements `pendingInstantWithdraws` and escrows the underlying in the strategy. [5](#0-4) 
3. `claimInstantWithdrawRequest` then burns the receipt, zeroes `instantWithdrawsRequests[user]` and pays out — but leaves `instantWithdrawsRequestsByEpoch[user][epoch]` and `instantWithdrawClaimsByEpoch[epoch]` untouched. [6](#0-5) 
4. If the same epoch ends in a borrower default, `finalizeDefaultRecovery` sets `defaultRecoveryEpoch = epochNumber` and `defaultInstantWithdrawsFinalized = true` whenever another user's instant request in that epoch is still unfunded. `defaultPendingClaimBasis` then includes the inflated `instantWithdrawClaimsByEpoch[epochNumber]`, depressing `defaultRecoveryPrice` for everyone. [7](#0-6) [8](#0-7) 
5. The attacker then calls `claimInstantWithdrawRequest` again. `_claimDefaultedInstantWithdrawRequest` reads `instantWithdrawsRequestsByEpoch[user][defaultEpoch]` — still holding the already-paid basis — decrements `instantWithdrawClaimsByEpoch`/`pendingInstantWithdraws`, burns `claimBasis` receipt tokens and transfers `claimBasis * defaultRecoveryPrice` from the recovery reserve a second time. [9](#0-8) 

The only obstacle is the `_burn(_user, claimBasis)` needing receipt-token balance, which the attacker can satisfy: post-default `requestWithdraw` mints fresh receipt tokens to the user (`_mint(_user, _amount)`), so after making a new (legitimate) post-default request the attacker holds enough strategy tokens to pass the burn. [10](#0-9) 

### Impact Explanation
Direct theft plus insolvency. The attacker is paid once at par before default and again at `defaultRecoveryPrice` from `defaultRecoveryReserve`. The reserve is fixed at finalization (`reserveAmount`), so every extra payout is taken from honest defaulted receipt holders and active LPs; once drained, later `_transferDefaultRecovery` calls revert, permanently freezing the remaining claims. Additionally, even without the second claim, the stale `instantWithdrawClaimsByEpoch` inflates `totalBasis` at finalization, diluting `defaultRecoveryPrice` for all claimants. [11](#0-10) 

### Likelihood Explanation
Requires: an epoch where instant withdraws are partially funded (so the attacker's receipt is claimable at par while `pendingInstantWithdraws` remains non-zero for another requester), then a borrower default at that epoch's `stopEpoch`. Borrower default is an honest-privileged sequence (allowed per scope — the attacker merely times their instant withdraw into the same epoch). An unprivileged KYC'd lender with tranche tokens suffices; no malicious privileged action is needed. The trigger condition (partial instant funding during an epoch that defaults) is an ordinary market outcome, not an attacker-fabricated state.

### Recommendation
In `claimInstantWithdrawRequest`, clear the per-epoch entries when paying a funded claim:

```solidity
uint256 reqEpoch = ...; // track or store the request epoch
instantWithdrawsRequestsByEpoch[_user][reqEpoch] -= amount;
instantWithdrawClaimsByEpoch[reqEpoch] -= amount;
```

Since instant receipts may span epochs, either store the request epoch alongside the receipt (like `lastWithdrawRequest`) or iterate/clear the current request epoch at request time. Symmetrically, ensure `defaultPendingClaimBasis` only counts receipts that are still unclaimed. A simpler invariant fix: decrement `instantWithdrawClaimsByEpoch[epoch]` inside `collectInstantWithdrawFunds`/`claimInstantWithdrawRequest` so the per-epoch claim basis always equals outstanding unfunded-plus-funded-unclaimed receipts.

### Proof of Concept
Foundry fork sketch (against `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
// setup: deposit as attacker (KYC'd) and honestUser; start epoch E
idleCDO.depositAA(amountWei);            // attacker
_depositWithUser(honestUser, amountWei); // other lender

// during epoch E, both request instant withdraw
uint256 reqA = cdoEpoch.requestWithdraw(attackerAA, address(AAtranche));   // attacker
vm.prank(honestUser);
cdoEpoch.requestWithdraw(honestAmount, address(AAtranche));

// CDO funds only the attacker's request (partial liquidity), leaving
// pendingInstantWithdraws != 0 for honestUser
// attacker claims at par
cdoEpoch.claimInstantWithdrawRequest();   // pays reqA; instantWithdrawsRequestsByEpoch[attacker][E] still = reqA

// borrower defaults at stopEpoch of epoch E; manager/owner finalize
// finalizeDefaultRecovery -> defaultRecoveryEpoch = E, recoveryPrice lowered
// because instantWithdrawClaimsByEpoch[E] still includes reqA

// attacker makes a small post-default requestWithdraw to regain receipt balance
cdoEpoch.requestWithdraw(small, address(AAtranche));

// second payout: burns stale basis, pulls reqA * defaultRecoveryPrice from reserve
cdoEpoch.claimInstantWithdrawRequest();
// assert reserve shortfall: honestUser's later claim reverts / receives less
```

Expected assertions: `instantWithdrawsRequestsByEpoch[attacker][E]` non-zero after the first claim; `defaultRecoveryPrice` lower than `reserve / honestBasis`; attacker's second claim transfers underlying while honest user's `_transferDefaultRecovery` reverts on insufficient reserve.

### Citations

**File:** contracts/strategies/idle/IdleCreditVault.sol (L247-257)
```text
    if (defaultRecoveryFinalized) {
      // user should first claim old already-funded withdraw requests before requesting new ones after default
      if (_hasWithdrawRequest(_user) || instantWithdrawsRequests[_user] != 0 || postDefaultRequests[_user] != 0) {
        revert NotAllowed();
      }
      // Preserve request/claim UX after default without increasing borrower-facing pendingWithdraws.
      // The CDO passes an already-haircut amount because finalization lowered virtualPrice first.
      _burn(msg.sender, _amount);
      _mint(_user, _amount);
      postDefaultRequests[_user] = _amount;
      return;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L356-375)
```text
  function requestInstantWithdraw(uint256 _amount, address _user) external {
    _onlyIdleCDO();
    _ensureDefaultRecoveryInitialized();
    // burn strategy tokens from cdo
    _burn(msg.sender, _amount);
  
    // mint equal amount of strategy tokens to the user as receipt, useful in case of default
    _mint(_user, _amount);

    // increase the instant withdraw requests for the user
    instantWithdrawsRequests[_user] += _amount;
    uint256 currentEpoch = epochNumber;
    // we record both per-user (old, kept for compatibility) and per-epoch so on
    // finalization we can distinguish "default-epoch pending instant receipts"
    // from old funded instant receipts.
    instantWithdrawsRequestsByEpoch[_user][currentEpoch] += _amount;
    instantWithdrawClaimsByEpoch[currentEpoch] += _amount;
    // increase the total instant withdraw requests
    pendingInstantWithdraws += _amount;
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L380-393)
```text
  function claimInstantWithdrawRequest(address _user) external {
    _onlyIdleCDO();
    if (defaultRecoveryFinalized && defaultInstantWithdrawsFinalized) {
      // Clear the defaulted-epoch instant receipt first, then continue so the same call can
      // also pay any older instant receipt that was already funded before default finalization.
      _claimDefaultedInstantWithdrawRequest(_user);
    }
    uint256 amount = instantWithdrawsRequests[_user];
    // burn strategy tokens from user
    _burn(_user, amount);

    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L398-403)
```text
  function collectInstantWithdrawFunds(uint256 _amount) external {
    _onlyIdleCDO();
    if (_amount == 0) return;
    pendingInstantWithdraws -= _amount;
    underlyingToken.safeTransferFrom(idleCDO, address(this), _amount);
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L644-649)
```text
  function defaultPendingClaimBasis() public view returns (uint256 basis) {
    basis = pendingWithdraws;
    if (pendingInstantWithdraws != 0) {
      basis += instantWithdrawClaimsByEpoch[epochNumber];
    }
  }
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L686-696)
```text
    uint256 reserveAmount = _recoveredAmount + prefundedReserve + defaultRecoveryReserve;
    // Recovery can be above par if the recovered funds exceed the computed basis.
    uint256 recoveryPrice = reserveAmount * RECOVERY_FULL / totalBasis;

    defaultRecoveryFinalized = true;
    defaultRecoveryReserve = reserveAmount;
    defaultRecoveryPrice = recoveryPrice;
    defaultRecoveryEpoch = epochNumber;
    // A non-zero pending instant bucket means current-epoch instant receipts were not fully funded
    // and must be paid through the same recovery ratio as normal pending receipts.
    defaultInstantWithdrawsFinalized = pendingInstantWithdraws != 0;
```

**File:** contracts/strategies/idle/IdleCreditVault.sol (L842-856)
```text
  function _claimDefaultedInstantWithdrawRequest(address _user) internal returns (uint256 claimBasis) {
    uint256 defaultEpoch = defaultRecoveryEpoch;
    claimBasis = instantWithdrawsRequestsByEpoch[_user][defaultEpoch];
    if (claimBasis == 0) return claimBasis;

    instantWithdrawsRequestsByEpoch[_user][defaultEpoch] = 0;
    instantWithdrawsRequests[_user] -= claimBasis;
    uint256 pending = pendingInstantWithdraws;
    // `pendingInstantWithdraws` is only the unfunded remainder. If this user's claim is larger,
    // the extra amount was already counted as prefunded reserve during default finalization.
    pendingInstantWithdraws = claimBasis >= pending ? 0 : pending - claimBasis;
    instantWithdrawClaimsByEpoch[defaultEpoch] -= claimBasis;
    _burn(_user, claimBasis);
    _transferDefaultRecovery(_user, (claimBasis * defaultRecoveryPrice) / RECOVERY_FULL);
  }
```
