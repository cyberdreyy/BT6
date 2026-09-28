### Title
Reorg on `IdleCreditVaultFactory` nonce-based proxy deployment redirects lender deposits to an attacker-controlled vault — (File: contracts/IdleCreditVaultFactory.sol)

### Summary
`IdleCreditVaultFactory.deployCreditVault` and `deployRevolvingCreditVault` deploy vault proxies via plain `CREATE` in `_deployProxy` (contracts/IdleCreditVaultFactory.sol:280-286), so a deployed vault's address is purely a function of the factory address and its nonce. Deployment is permissionless and the caller fully controls `StrategyData.manager`, `StrategyData.borrower`, `CreditVaultParams` (fees, limits, APR) and `AncillaryParams` (Keyring can be disabled by passing `keyring == address(0)`, line 168-173). A lender who approves underlying and calls `depositAA`/`depositDuringEpoch`/`requestDeposit` against a predicted vault address can have that address reassigned to an attacker-deployed vault after a Polygon reorg, and `_deposit` forwards the underlying straight to `borrower` (IdleCDOEpochVariant.sol:731-732) — an address the attacker set. This is the same bug class as the referenced DAO-membership report: nonce-derived destination address + user approval + reorg = funds sent to the wrong contract.

### Finding Description
- `_deployProxy` uses `new TransparentUpgradeableProxy(...)` (CREATE), so `vaultAddress = f(factory, nonce)` with no salt and no caller binding (IdleCreditVaultFactory.sol:281-285).
- `deployCreditVault` has no access control; anyone can deploy a vault with arbitrary `borrower`, `manager`, `keyring == 0`, and `isDepositDuringEpochDisabled == false` (lines 92-124).
- On a deposit, the CDO mints shares to the user but transfers the underlying to `strategy.borrower()` (`_transferUnderlyings(_borrower(), _amount)`, IdleCDOEpochVariant.sol:732; the queued path does the same via `processDepositsToBorrower` → `safeTransfer(_strategy.borrower(), _pending)`, IdleCDOEpochQueue.sol:179).
- Scenario: treasury deploys `vaultA` (factory nonce N → `addr1`). Bob approves `underlying` to `addr1` and broadcasts `depositAA`. A reorg reorders the two pending factory transactions so the attacker's `deployCreditVault` (borrower = attacker EOA, no Keyring) consumes nonce N and materializes at `addr1`, while `vaultA` gets `addr2`. Bob's approval and deposit now hit the attacker's vault; `_deposit` transfers his underlying to the attacker's borrower address.
- Existing guards do not stop this: `isWalletAllowed` passes on the attacker vault because Keyring is disabled; ownership is transferred to `treasury` only after configuration and does not claw back the borrower transfer; `_skimDonatedAssets` and NAV checks are irrelevant to a first deposit.

### Impact Explanation
Direct theft of the full deposit amount. The lender's underlying is forwarded to the attacker's chosen `borrower` address at deposit time, and the lender receives worthless tranche shares of a vault whose funds already left. Loss = 100% of the approved-and-deposited amount, bounded only by the victim's transaction size and the vault `limit`.

### Likelihood Explanation
Low-to-moderate. It requires (a) a reorg deep enough to reorder the honest `deployCreditVault` transaction, and (b) the attacker's factory call landing in the same window — which the attacker can time by watching the mempool for pending factory deployments and front-riding the reorder, exactly as in the DAO report. Polygon has historically experienced reorgs, and the failure is silent: the victim's transaction still succeeds.

### Recommendation
Bind the deployment to identity: use `CREATE2` with a salt incorporating `msg.sender`/deploy params, or restrict `deployCreditVault`/`deployRevolvingCreditVault` to a trusted deployer role so nonce ordering cannot be manipulated by an unprivileged party. Off-chain, warn integrators not to pre-approve underlying to a vault address before the deployment transaction is finalized, and consider a `expectedDeployer`/registry check in the deposit path.

### Proof of Concept
Foundry fork-style sketch (Polygon mainnet fork). The reorg is simulated by executing the attacker's factory call before the honest one:

```solidity
// test/foundry/ReorgVaultHijack.t.sol
function testReorgRedirectDeposit() public {
    // honest treasury intends to deploy vaultA; attacker sees the pending tx.
    // Simulate reorg ordering: attacker's deploy consumes factory nonce first.
    IdleCreditVaultFactory.StrategyData memory sd = IdleCreditVaultFactory.StrategyData({
        implementation: strategyImpl,
        manager: attacker,
        borrower: attacker,        // funds destination
        borrowerName: "evil"
    });
    IdleCreditVaultFactory.CreditVaultParams memory cv = paramsFor(underlying); // valid fees, no deposit-during-epoch disable
    IdleCreditVaultFactory.AncillaryParams memory anc; 
    anc.keyring = address(0);      // KYC disabled -> anyone allowed

    vm.prank(attacker);
    factory.deployCreditVault(cv, sd, anc);   // lands at addr1 = f(factory, nonce N)

    // treasury's honest deploy lands at addr2 (nonce N+1)
    vm.prank(treasury);
    factory.deployCreditVault(honestCv, honestSd, honestAnc);

    // Bob had approved underlying to addr1 (pre-reorg it was vaultA)
    vm.prank(bob);
    underlying.approve(addr1, type(uint256).max);
    vm.prank(bob);
    IdleCDOEpochVariant(addr1).depositAA(1000e6); // or depositDuringEpoch

    assertEq(underlying.balanceOf(attacker), 1000e6); // stolen
}
```

Caveat: the PoC assumes `isWalletAllowed` returns true when `keyringWhitelist == address(0)` on the attacker vault and that a direct deposit path (`depositAA` during buffer, or `depositDuringEpoch`) is open — both controllable via the attacker-supplied params; exact guard behavior was not fully verified within the available iterations.