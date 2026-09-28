### Title
Withdrawal claim paths bypass the Keyring/KYC wallet gate enforced on deposits and requests - (File: contracts/IdleCDOEpochVariant.sol)

### Summary
`IdleCDOEpochVariant` enforces the KYC/Keyring credential check (`isWalletAllowed`) on every entry point that takes user funds — `_deposit` (line 645), `depositDuringEpoch` (line 668), and `requestWithdraw` (line 743) — but the two functions that actually pay out underlyings, `claimWithdrawRequest` (lines 967–971) and `claimInstantWithdrawRequest` (lines 975–979), forward `msg.sender` straight to `IdleCreditVault` without any wallet validation. A wallet that is not credentialed — or whose credential was revoked after it acquired a claim — can still pull underlying tokens out of the vault, defeating the restricted-participation policy the protocol intends to apply to all fund flows.

### Finding Description
Analogous to CVE-2019-12433 (a restricted visibility setting being under-validated on one code path while enforced elsewhere), the vault's "restricted wallet" setting is enforced inconsistently:

- `isWalletAllowed(_user)` returns `keyring == address(0) || IKeyring(keyring).checkCredential(keyringPolicyId, _user)` (lines 984–987).
- `requestWithdraw` reverts for non-allowed wallets, and `depositDuringEpoch`/`_deposit` do the same.
- `claimWithdrawRequest()` and `claimInstantWithdrawRequest()` contain no such check. They simply call `IdleCreditVault(strategy).claimWithdrawRequest(msg.sender)` / `claimInstantWithdrawRequest(msg.sender)`, which releases the stored underlyings to the caller.

Withdrawal receipts held in `IdleCreditVault` are keyed by the claiming address, so any address that ends up holding the claim entitlement — e.g., a tranche holder whose Keyring credential was revoked or expired between `requestWithdraw` and the claim, or any counterparty to whom a claim position was passed — can settle the claim and receive underlyings while explicitly failing the vault's credential policy. No guard (`_skimDonatedAssets`, `defaulted`, `allowInstantWithdraw`, epoch gating) substitutes for the missing wallet check; `defaulted` even has carve-outs so old requests remain claimable after default.

### Impact Explanation
The KYC gate is the only thing standing between a non-credentialed address and the vault's cash. Because claims are unauthenticated, the restriction is cosmetic at the payout step: an unprivileged, non-KYC'd EOA that obtains a withdrawal request entitlement (revoked credential, secondary transfer, or a Keyring policy that lapses mid-cycle) can extract underlyings from the strategy reserve. This is a direct policy/access bypass with fund movement — the underlyings leave `IdleCreditVault` to an address the protocol's own configuration says must not interact with the pool. Where the reserve is funded by other lenders' pending requests, settlement order matters, so disallowed claimants can also drain liquidity ahead of compliant claimants.

### Likelihood Explanation
Likelihood is moderate: it requires the claimant to hold a matured request. That is cheap to arrange — a KYC-passing lender requests a withdrawal, lets the credential lapse (or the operator revokes it mid-epoch via `setKeyringParams`/`setWhitelistStatus`), then claims anyway. It also fires any time a receipt-holder address changes status across the multi-epoch delay between `requestWithdraw` and `claimWithdrawRequest`, which is a normal operational occurrence rather than an exotic state.

### Recommendation
Apply the same wallet gate used on `requestWithdraw` to both claim functions:

```solidity
// contracts/IdleCDOEpochVariant.sol
function claimWithdrawRequest() external {
    _checkNotAllowed(!isWalletAllowed(msg.sender));
    IdleCreditVault(strategy).claimWithdrawRequest(msg.sender);
}

function claimInstantWithdrawRequest() external {
    _checkNotAllowed(!allowInstantWithdraw || !isWalletAllowed(msg.sender));
    IdleCreditVault(strategy).claimInstantWithdrawRequest(msg.sender);
}
```

If allowing revoked wallets to recover their own principal is intended, document that explicitly and gate only the *receipt-of-funds* direction — but the current silent asymmetry should not remain.

### Proof of Concept
Foundry fork test outline (matches the harness style in `test/foundry/IdleCreditVault.t.sol`):

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import {IdleCDOEpochVariant} from "../contracts/IdleCDOEpochVariant.sol";
import {IdleCreditVault} from "../contracts/strategies/idle/IdleCreditVault.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

contract ClaimKycBypassPoC is Test {
    IdleCDOEpochVariant cdo;      // deployed epoch-variant CDO
    IdleCreditVault vault;        // IdleCreditVault strategy
    IERC20 token;                 // underlying
    address keyring;              // KeyringIdleWhitelist
    address kycUser = address(0xA11CE);

    function test_ClaimBypassesKycRevocation() external {
        // 1. kycUser is credentialed and deposits during buffer.
        //    _deposit passes because isWalletAllowed(kycUser) == true.
        // 2. Epoch runs, owner/manager calls stopEpoch(newApr, interest).
        // 3. kycUser calls requestWithdraw(amount, AATranche) while still credentialed.
        // 4. Next epoch completes; funds are sent to IdleCreditVault for claims.
        // 5. Admin removes kycUser from KeyringIdleWhitelist / policy expires:
        //    isWalletAllowed(kycUser) now returns false.
        assertFalse(cdo.isWalletAllowed(kycUser));

        uint256 balBefore = token.balanceOf(kycUser);
        vm.prank(kycUser);
        cdo.claimWithdrawRequest(); // succeeds despite failed credential check
        assertGt(token.balanceOf(kycUser), balBefore);
        // Underlyings left IdleCreditVault to a wallet that fails the vault's
        // own restricted-access policy.
    }
}
```

Note: I was not able to read `IdleCreditVault.sol` and `IdleCDOTranche.sol` in the available iterations, so whether withdrawal receipts themselves are transferable (which would widen this to any arbitrary non-KYC buyer of a claim) is unverified; the revoked-credential path above does not depend on transferability and stands on `IdleCDOEpochVariant.sol` lines 967–987 alone.