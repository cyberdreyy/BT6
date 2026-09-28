### Title
Permissionless factory lets anyone deploy a credit vault attributed to a legitimate borrower - (File: contracts/IdleCreditVaultFactory.sol)

### Summary
`deployCreditVault` and `deployRevolvingCreditVault` in `IdleCreditVaultFactory` are fully permissionless. The caller supplies `strategyData.borrower`, `strategyData.borrowerName`, `strategyData.manager`, and the `underlying` token, and the factory emits `CreditVaultDeployed(...)` presenting the new vault as a genuine factory deployment. Nothing verifies that the deployer is entitled to act for the named borrower. An attacker can therefore deploy a vault labeled with a legitimate borrower's name and address while setting themselves as the actual `borrower`, so pooled lender funds are borrowable by the attacker.

### Finding Description
In `deployCreditVault` (contracts/IdleCreditVaultFactory.sol:92-124) any EOA can call the function. `_deployBaseCreditVault` initializes `IdleCreditVault` with `strategyData.borrower` and `strategyData.borrowerName` taken verbatim from calldata (lines 182-193). The emitted `CreditVaultDeployed` event (lines 25-32, emitted at lines 116-123 and 158-165) is indistinguishable from a vault deployed by Idle for a real credit deal, and there is no registry, signature, or allowlist binding the borrower identity to the deployer.

This is the same bug class as the Gitcoin `RoundFactory.create` issue: a caller-controlled attribution parameter (`borrower`/`borrowerName` here, `ownedBy` there) is baked into a deployed contract and surfaced through a canonical factory event that a front-end/indexer has no way to distinguish from a legitimate deployment.

Critically, unlike the Gitcoin case, the consequence is not merely reputational: the `borrower` address on `IdleCreditVault` is the account entitled to draw the pooled lender funds during a running epoch. The attacker-controlled fake vault is a fully functional credit facility whose borrower is the attacker.

### Impact Explanation
If a dApp, indexer, or lender dashboard enumerates vaults via `CreditVaultDeployed` events (or trusts `borrowerName`/borrower metadata), an attacker-deployed vault impersonating a reputable borrower can attract KYC'd lender deposits. During the running epoch the attacker, as `borrower`, calls the strategy's borrow path and takes the pooled underlying. Depositors' funds are transferred out; recovery depends entirely on the attacker repaying, so the realistic outcome is direct theft of deposited underlying up to the vault limit. Loss is bounded only by `cvParams.limit` chosen by the attacker, i.e. effectively unbounded.

### Likelihood Explanation
Requires off-chain discovery/trust (victims must find and deposit into the fake vault, and must pass whatever Keyring policy the attacker configures — the attacker can deploy with `keyring == address(0)` or a permissive policy to maximize reach). The on-chain preconditions are trivial: one permissionless call. Impact is high (full loss of deposits) but likelihood depends on the phishing/UX layer, consistent with the original Medium rating.

### Recommendation
Gate vault deployment: make `deployCreditVault`/`deployRevolvingCreditVault` callable only by `treasury` (or a dedicated deployer role). Alternatively, if permissionless deployment is intentional, remove `borrowerName` from caller-controlled input or maintain an on-chain registry of approved borrower identities so the event/dApp can distinguish sanctioned deployments.

### Proof of Concept
Foundry sketch (fork test against the repo's `test/foundry/IdleCreditVaultFactory.t.sol` setup):

```solidity
function test_fakeVaultAttributedToLegitBorrower() public {
    // factory already deployed with honest treasury/proxyAdmin (see _deployFactory)
    address legitBorrower = 0xLegit...; // known borrower entity
    address attacker = makeAddr("attacker");
    address lender  = makeAddr("kycLender");

    IdleCreditVaultFactory.CreditVaultParams memory cv = ...; // attacker-chosen, passes _checkMinimumFees
    IdleCreditVaultFactory.StrategyData memory sd = IdleCreditVaultFactory.StrategyData({
        implementation: address(strategyImpl),
        manager: attacker,              // attacker is guardian/manager of the fake vault
        borrower: attacker,             // attacker is entitled to borrow pooled funds
        borrowerName: "Legit Borrower Ltd" // impersonated attribution
    });
    IdleCreditVaultFactory.AncillaryParams memory anc = ...; // keyring = address(0) -> no KYC gate

    vm.prank(attacker);
    factory.deployCreditVault(cv, sd, anc); // emits CreditVaultDeployed indistinguishable from real

    // lender deposits underlying into cv during buffer; manager starts epoch;
    // attacker borrows pool funds via IdleCreditVault(strategy).borrow(...) and never repays.
    assertGt(underlying.balanceOf(attacker), lenderDeposit - dust);
}
```

Assumed/unverified details: exact names of `IdleCreditVault.borrow`/deposit entry points and the borrow-time checks (the borrow path was not fully read in this pass); the core flaw — unverified `borrower`/`borrowerName` in a permissionless deploy — is confirmed in `IdleCreditVaultFactory.sol`.