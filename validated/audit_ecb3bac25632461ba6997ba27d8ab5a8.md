### Title
Post-default instant withdraw requests bypass the recovery haircut — `requestInstantWithdraw` left unpatched where `requestWithdraw` was guarded - ([contracts/strategies/idle/IdleCreditVault.sol](contracts/strategies/idle/IdleCreditVault.sol))

### Summary
This codebase contains an incomplete-fix pattern structurally identical to the external report: the Froxlor CVE-2026-30932 fix validated LOC/RP/SSHFP/TLSA record content but left TXT unsanitized. Here, `requestWithdraw` was hardened with a `defaultRecoveryFinalized` branch that routes post-default requests through the haircut-aware `postDefaultRequests` flow and blocks users with stale receipts, but the parallel `requestInstantWithdraw` entry point received no equivalent guard. After `finalizeDefaultRecovery` runs, new instant receipts can still be created and are later paid through `claimInstantWithdrawRequest` at full value, outside the `defaultRecoveryPrice` haircut accounting.

### Finding Description
`requestWithdraw` explicitly handles the post-default regime:

- `IdleCreditVault.sol:247-257`: if `defaultRecoveryFinalized`, it reverts unless `_hasWithdrawRequest`, `instantWithdrawsRequests`, and `postDefaultRequests` are all zero, then mints an already-haircut receipt into `postDefaultRequests` and returns — never touching `pendingWithdraws`.

`requestInstantWithdraw` has no such branch:

- `IdleCreditVault.sol:356-375`: unconditionally burns CDO strategy tokens, mints a full receipt to the user, and increments `instantWithdrawsRequests[_user]`, `instantWithdrawsRequestsByEpoch[_user][epochNumber]`, `instantWithdrawClaimsByEpoch[epochNumber]`, and `pendingInstantWithdraws`.

The asymmetry breaks at claim time in `claimInstantWithdrawRequest` (`IdleCreditVault.sol:380-393`):

1. `defaultInstantWithdrawsFinalized` was frozen at finalization (`IdleCreditVault.sol:696`), so `_claimDefaultedInstantWithdrawRequest` only clears basis keyed to `defaultRecoveryEpoch` (`IdleCreditVault.sol:843-855`). Post-default receipts added under a later `epochNumber` are untouched by this clearing step.
2. The function then reads `instantWithdrawsRequests[_user]` — which now includes the post-default amount — burns it, and pays it 1:1 via `_transferFundedClaim` (`IdleCreditVault.sol:387-392`), skipping the `defaultRecoveryPrice` haircut entirely.

`_transferFundedClaim` (`IdleCreditVault.sol:897-907`) only protects the reserve itself (`balance - reserve < amount` reverts). Any strategy balance above `defaultRecoveryReserve` — late borrower repayments, previously funded but unclaimed receipts, or recovery dust — is payable to these un-haircut post-default receipts.

If no surplus exists, the same flaw causes a permanent `NotAllowed` revert for the user and leaves `pendingInstantWithdraws` and `instantWithdrawClaimsByEpoch` permanently inflated for a post-finalization epoch, corrupting accounting that `defaultPendingClaimBasis` (`IdleCreditVault.sol:644-649`) assumes is stable.

The fix applied to the normal path is also under-scoped in the other direction: `requestInstantWithdraw` does not check `_hasWithdrawRequest`/`postDefaultRequests`, so even before default it lets a user stack an instant receipt on top of a pending `postDefaultRequests` entry — a combination `requestWithdraw` explicitly forbids.

### Impact Explanation
- Direct theft at par: post-default instant receipts are minted without the `defaultRecoveryPrice` haircut (unlike `postDefaultRequests`, which receive a pre-haircut amount from the CDO). If the strategy later holds non-reserve underlyings, the attacker claims them 1:1, ahead of funded claimants and diluting the intended recovery distribution.
- Permanent freezing / DoS: when `balance - reserve < amount`, every `claimInstantWithdrawRequest` for that user reverts permanently; the polluted `instantWithdrawsRequests` can never be unwound since there is no delete path for instant requests.
- Accounting corruption: `pendingInstantWithdraws` and `instantWithdrawClaimsByEpoch` grow in an epoch that no finalization will ever process, since `defaultRecoveryFinalized` is a one-shot latch.

Quantified worst case: any non-reserve balance `X` in the strategy can be drained at par by a post-default instant receipt of size `X`, versus the `X * defaultRecoveryPrice / 1e18` it should be worth.

### Likelihood Explanation
High on the vault side: `requestInstantWithdraw` is callable any time, with no epoch-running gate and no default gate — the only check is `_onlyIdleCDO()`. Reachability from an unprivileged user depends on the CDO routing `requestWithdraw` to the strategy's instant path after `defaulted()` is set; I was unable to fully verify `IdleCDOEpochVariant.requestWithdraw`'s post-default branching within this pass (my grep returned match counts only, not file contents). If the CDO exposes any instant-withdraw path post-default — which the prefunded/queue `isEpochInstant` machinery and `getInstantWithdrawFunds` flow suggest is exercised around epoch stops and defaults — the bug is directly reachable by any KYC'd tranche holder. Even if CDO gating exists today, this is a latent vault-level invariant break identical in kind to the unpatched TXT record type.

### Recommendation
Mirror the `requestWithdraw` post-default logic in `requestInstantWithdraw`:

```solidity
// contracts/strategies/idle/IdleCreditVault.sol, requestInstantWithdraw
if (defaultRecoveryFinalized) {
  if (_hasWithdrawRequest(_user) || instantWithdrawsRequests[_user] != 0 || postDefaultRequests[_user] != 0) {
    revert NotAllowed();
  }
  _burn(msg.sender, _amount);
  _mint(_user, _amount);
  postDefaultRequests[_user] = _amount; // reuse haircut-aware bucket
  return;
}
```

Alternatively, revert outright in `requestInstantWithdraw` when `defaultRecoveryFinalized` if instant withdrawals are not meant to exist post-default, and add a symmetric guard preventing new normal requests while `instantWithdrawsRequests[_user] != 0` if that stacking is unintended.

### Proof of Concept
Foundry fork sketch (vault side; prank `idleCDO` since the strategy only checks `msg.sender == idleCDO` — replace the CDO invocation with `cdoEpoch.requestWithdraw` once the post-default instant routing in `IdleCDOEpochVariant.sol` is confirmed):

```solidity
function testPostDefaultInstantWithdrawBypass() external {
  // 1. Deposit AA/BB, run epoch, request normal + instant withdraws
  idleCDO.depositAA(1000e6);
  idleCDO.depositBB(1000e6);
  _startEpochAndCheckPrices(0);
  _requestWithdrawWithUser(user1, tranches);      // normal request
  cdoEpoch.requestWithdraw(0, address(AAtranche)); // instant request (buffer)

  // 2. Borrower defaults; manager finalizes recovery at < 1e18 price
  _checkDefault();
  vm.prank(manager);
  cdoEpoch.finalizeDefaultRecovery(recovered, recoverySource);
  assertTrue(vault.defaultRecoveryFinalized());
  assertLt(vault.defaultRecoveryPrice(), 1e18);

  // 3. BUG: post-default instant request still succeeds — no default gate
  deal(trancheAddr, attacker, amount);
  vm.prank(idleCDO);
  vault.requestInstantWithdraw(amount, attacker); // reverts? NO — missing check
  assertGt(vault.instantWithdrawsRequests(attacker), 0);
  assertGt(vault.pendingInstantWithdraws(), 0);   // polluted for a dead epoch

  // 4. Late borrower repayment arrives at the strategy (non-reserve funds)
  deal(address(underlying), address(vault), vault.defaultRecoveryReserve() + amount);

  // 5. Attacker claims the un-haircut receipt at par, bypassing defaultRecoveryPrice
  vm.prank(idleCDO);
  vault.claimInstantWithdrawRequest(attacker);
  assertEq(underlying.balanceOf(attacker), amount); // paid 1:1, no haircut
}
```

Expected today: step 3 does not revert and step 5 pays `amount` instead of `amount * defaultRecoveryPrice / 1e18`. The one element I could not confirm in this pass is whether `IdleCDOEpochVariant` still forwards instant requests once `defaulted()` is true; if it does not, step 3 must be issued via `vm.prank(idleCDO)` and the finding stands as a vault-level missing-guard/incomplete-fix rather than a user-reachable exploit.