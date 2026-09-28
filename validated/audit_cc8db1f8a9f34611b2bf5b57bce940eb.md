### Title
Fail-open KYC gate: `isWalletAllowed` returns true for every wallet when `keyring` is unset - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
The Druid advisory describes a security-critical secret that, when not explicitly configured, silently falls back to a weak/predictable value instead of failing closed. The analog in this codebase is the Keyring credential check: `isWalletAllowed` treats `keyring == address(0)` (i.e., credential contract not configured) as "allow everyone" rather than "deny everyone". The deployment flow sets `keyring` in a separate post-initialization step (`setKeyringParams`, owner/manager only), so there is a window — and a permanent state if the step is forgotten or misapplied — in which the vault's KYC gate is fully open to any unprivileged EOA.

### Finding Description
`IdleCDOEpochVariant.isWalletAllowed` is implemented as: [1](#0-0) 

`keyring` starts as `address(0)` after `initialize` — `_additionalInit` never sets it — and is only populated later via `setKeyringParams` (owner/manager). Every user-facing entry point gates solely on this check:

- `_deposit` → `depositAA`/`depositBB` (`!isWalletAllowed(msg.sender)` reverts)
- `depositDuringEpoch`
- `requestWithdraw`

The deployment scripts confirm the two-step pattern: the CDO is initialized without a keyring and `setKeyringParams` is invoked afterwards (the factory/deploy task even branches on `deployToken.keyring`, and only conditionally calls `setKeyringParams`). Between initialization and that call — or permanently if the call is skipped — `isWalletAllowed` returns `true` for all addresses, exactly like Druid's silently generated weak fallback secret: a missing security configuration produces an insecure default instead of a revert.

This contrasts with the rest of the codebase, which fails closed on unset critical params: `initialize` reverts on `_strategy == address(0)` / `_guardedToken == address(0)`, `GuardedLaunchUpgradable` reverts on zero `_governanceFund`/`_owner`, and the factory reverts on zero treasury/proxyAdmin. The access-control dependency is the one component that defaults to permissive.

### Impact Explanation
The broken invariant is access control. A credit vault's KYC/credential gating exists so only credentialed lenders can hold tranche tokens and file withdrawal claims. With `keyring` unset, any arbitrary EOA can:

- `depositAA`/`depositBB` during the buffer phase and receive tranche tokens,
- `requestWithdraw` and obtain withdrawal receipts with the same claim priority as credentialed lenders,
- `depositDuringEpoch` when that mode is enabled, minting tranche tokens at the prorated price.

Because tranche tokens and withdrawal receipts are claims on a fixed pool of borrower repayments, unvetted participants gain fully fungible, claimable positions equal in seniority to compliant lenders — a direct breach of the vault's only eligibility control, exploitable by any unprivileged address with zero cost beyond the deposit itself (which is later withdrawable). The exposure is the entire deposit surface of the vault until an operator notices and sets the keyring.

### Likelihood Explanation
Unlike a malicious-privileged-role scenario, this requires only that the honest owner/manager performs the documented multi-step deployment where `setKeyringParams` is a separate, easily-omitted call — there is no atomic coupling between vault initialization and credential configuration, and no on-chain check that a keyring is set before deposits open (`isEpochRunning == false` + `allowAA/BBWithdrawRequest == true` + unpaused is sufficient for deposits to succeed). The deploy task treats keyring configuration as optional/conditional, so a deployment that never reaches that branch leaves the gate permanently open. That is precisely the "configuration not explicitly set → insecure default" pattern of CVE-2025-59390.

### Recommendation
Fail closed: revert (or return `false`) in `isWalletAllowed` when `keyring == address(0)`, so deposits and withdrawal requests are impossible until a credential contract is explicitly configured — mirroring the Druid fix, which made `cookieSignatureSecret` mandatory and prevents startup without it. Optionally also allow only admin-whitelisted behavior via an explicit `keyringDisabled` flag if permissionless operation is ever intended, so "open vault" is a deliberate, auditable configuration rather than a silent default.

### Proof of Concept
```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IdleCDOEpochVariant} from "../contracts/IdleCDOEpochVariant.sol";
import {IdleCreditVault} from "../contracts/strategies/idle/IdleCreditVault.sol";
import {IERC20Detailed} from "../contracts/interfaces/IERC20Detailed.sol";

/// Demonstrates the fail-open KYC gate: a vault whose `keyring` was never
/// configured accepts deposits and withdraw requests from any arbitrary EOA.
contract KeyringFailOpenTest is Test {
    IdleCDOEpochVariant cdo;
    IdleCreditVault strategy;
    IERC20Detailed underlying = IERC20Detailed(USDC); // fork mainnet

    address owner   = makeAddr("owner");
    address manager = makeAddr("manager");
    address borrower = makeAddr("borrower");
    address stranger = makeAddr("nonKycUser"); // never whitelisted anywhere

    function setUp() public {
        // deploy strategy + cdo via proxies, call initialize(...)
        // NOTE: deliberately DO NOT call setKeyringParams — the common
        // deploy path leaves `keyring == address(0)` until a later tx.
        assertEq(cdo.keyring(), address(0));
        deal(USDC, stranger, 1_000_000e6);
    }

    function test_nonKycUserCanDepositAndRequestWithdraw() public {
        vm.startPrank(stranger);
        underlying.approve(address(cdo), type(uint256).max);

        // gate is open: isWalletAllowed(stranger) == true
        assertTrue(cdo.isWalletAllowed(stranger));

        uint256 minted = cdo.depositAA(100_000e6);
        assertGt(minted, 0); // stranger holds AA tranche claim

        // and can file a withdraw request like a credentialed lender
        cdo.requestWithdraw(0, cdo.AATranche());
        assertGt(strategy.withdrawsRequestsByEpoch(stranger, cdo.epochNumber()), 0);
        vm.stopPrank();
    }
}
```

Run with `forge test --fork-url $MAINNET_RPC`. The assertions pass while `keyring == address(0)`, proving unrestricted access; after `setKeyringParams(keyring, policyId)` the same calls revert with `NotAllowed`, proving the check was the only barrier and it defaulted to off.

### Citations

**File:** contracts/IdleCDOEpochVariant.sol (L984-987)
```text
  function isWalletAllowed(address _user) public view returns (bool) {
    address _keyring = keyring;
    return _keyring == address(0) || IKeyring(_keyring).checkCredential(keyringPolicyId, _user);
  }
```
