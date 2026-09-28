### Title
Instant-withdraw claims skip the epoch-maturity gate enforced on normal withdraw claims, letting pending receipts drain funded underlyings - ([File: contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
`claimInstantWithdrawRequest` pays out `instantWithdrawsRequests[_user]` immediately, without the maturity check applied to normal claims in `_claimFundedWithdrawRequest` (`epochNumber <= lastWithdrawRequest[_user]` reverts). A user who opens an instant-withdraw request in a running epoch can claim it in the same epoch, spending underlyings that belong to active LPs rather than borrower-funded instant-withdraw proceeds. This mirrors the external bug class: a secondary validation path (delegated/instant) missing the integrity checks applied on the primary path (top-level/normal claims).

### Finding Description
In `IdleCreditVault`, normal withdraw receipts are deliberately locked until their request epoch has ended:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol:326
if (IIdleCDOEpochVariant(idleCDO).epochEndDate() != 0 && (epochNumber <= lastWithdrawRequest[_user])) {
    revert NotAllowed();
}
```

The instant path applies no equivalent gate (`IdleCreditVault.sol:380-393`):

```solidity
function claimInstantWithdrawRequest(address _user) external {
    _onlyIdleCDO();
    ...
    uint256 amount = instantWithdrawsRequests[_user];
    _burn(_user, amount);
    instantWithdrawsRequests[_user] = 0;
    _transferFundedClaim(_user, amount);
}
```

`requestInstantWithdraw` (lines 356-375) burns the CDO's strategy tokens, mints a receipt to the user, and records `instantWithdrawsRequestsByEpoch[user][epochNumber]` plus `pendingInstantWithdraws`. Funding only arrives later via `collectInstantWithdrawFunds`, which pulls tokens from the CDO at stop-epoch. Between request and funding, the vault's underlying balance is the backing of active tranche holders. `_transferFundedClaim` only guards the *default recovery reserve*, not ordinary solvency, so an unfunded instant receipt pays out of LP backing immediately.

The per-epoch mapping `instantWithdrawsRequestsByEpoch` proves the contract already tracks request epochs, but only uses them for default-finalization accounting — never to verify a request's epoch has actually been funded before paying.

### Impact Explanation
Any KYC-passing tranche holder can, during a running epoch:
1. Deposit, then call `requestInstantWithdraw` via the CDO.
2. Call `claimInstantWithdrawRequest` in the same transaction/epoch.
3. Receive underlying tokens drawn from the vault's LP backing, before the borrower has returned any funds and before `pendingInstantWithdraws` was funded by `collectInstantWithdrawFunds`.

Each request extracts value at par from active LPs' collateral. If `pendingInstantWithdraws` is later funded at stopEpoch, the theft is masked as insolvency of the funded bucket; if the pool is closed or defaults first, the loss is socialized to remaining LPs. Direct theft quantified up to the vault's liquid underlying balance.

### Likelihood Explanation
Requires only a standard unprivileged user flow (deposit → requestInstantWithdraw → claim). No privileged collusion needed; gating must be enforced inside the vault because the CDO's claim call is the user's own action. The only precondition is that `isWalletAllowed`/instant-withdraw mode is enabled on the pool, which is a normal pool configuration.

### Recommendation
Track a last-instant-request epoch per user (or reuse `instantWithdrawsRequestsByEpoch`) and enforce in `claimInstantWithdrawRequest` the same maturity rule as `_claimFundedWithdrawRequest`: revert when `epochNumber <= <request epoch>` while `epochEndDate() != 0`, unless the request epoch's `pendingInstantWithdraws` share has been collected via `collectInstantWithdrawFunds`. Alternatively, gate instant claims on a per-epoch funded flag set by `collectInstantWithdrawFunds`.

### Proof of Concept
Foundry fork PoC sketch (against the existing `test/foundry/IdleCreditVault.t.sol` harness):

```solidity
// setup: pool in buffer/running epoch, KYC'd attacker deposits AA
_depositWithUser(attacker, amount, true);   // deposits via idleCDO + cdoEpoch

// same epoch: request instant withdraw then immediately claim
vm.startPrank(attacker);
cdoEpoch.requestInstantWithdraw(amount, address(AAtranche));
uint256 balBefore = underlying.balanceOf(attacker);
cdoEpoch.claimInstantWithdrawRequest();     // -> strategy.claimInstantWithdrawRequest(attacker)
vm.stopPrank();

// attacker was paid from vault LP backing even though no
// collectInstantWithdrawFunds/stopEpoch ever funded the request
assertGt(underlying.balanceOf(attacker) - balBefore, 0);
assertGt(strategy.pendingInstantWithdraws(), 0); // still unfunded
```

Contrast: the equivalent normal-flow sequence (`requestWithdraw` → `claimWithdrawRequest` in the same epoch) reverts at `IdleCreditVault.sol:326-328`, demonstrating the missing validation on the instant path.

Note: I could not fully confirm whether `IdleCDOEpochVariant.claimInstantWithdrawRequest` adds an equivalent epoch check at the CDO layer (the grep match content wasn't returned). If the CDO itself blocks same-epoch instant claims, this finding's exploitability depends on that check's absence there; the strategy-side asymmetry remains regardless.