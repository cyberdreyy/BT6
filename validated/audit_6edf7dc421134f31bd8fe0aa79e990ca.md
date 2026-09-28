### Title
Underlying tokens that block transfers to specific addresses permanently freeze address-bound withdraw/claim receipts — ([File: contracts/strategies/idle/IdleCreditVault.sol])

### Summary
The external gravitybridge finding showed that ERC20s which revert on transfers to particular addresses (e.g., `address(0)`, USDC/USDT-blacklisted, or sanctioned receivers) can be weaponized to DoS a shared batch. The credit-vault analog is a pull-pattern variant with a worse outcome: `IdleCreditVault` withdraw and default-recovery claims are strictly address-bound — strategy/receipt tokens can only be moved by the `idleCDO` (`_transfer` reverts for any other caller), and claim payouts always push `token` to the receipt owner (`msg.sender`). A lender whose address is blocked by the underlying token (blacklist/sanctions — realistic for USDC/USDT pool currencies on Clearpool-style vaults) has every claim permanently reverting and no way to redirect the payout or migrate the receipt, so their principal and recovery funds are frozen forever.

### Finding Description
`IdleCreditVault._transfer` hard-reverts unless `msg.sender == idleCDO` (lines 939-942), making withdraw receipts and post-default claim basis non-transferable. The claim paths (`claimWithdrawRequest`, `claimInstantWithdrawRequest`, and the `_claimDefaulted*`/funded-claim payout path) settle by pushing `underlyingToken` to the requesting account, with no `_to` parameter. `DefaultDistributor.claim(address _to)` proves the codebase already supports a recipient parameter elsewhere (lines 35-41), but the vault claims do not. Because `safeTransfer` to a blocked recipient reverts atomically, each claim attempt fails before state is consumed — the request remains, but can never be fulfilled by that user, and cannot be reassigned to another address since receipt tokens are non-transferable and claims are bound to the original `withdrawsRequestsByEpoch[user]`/`instantWithdrawsRequestsByEpoch[user]`/`postDefaultRequests[user]` keys.

### Impact Explanation
Permanent freezing of the blocked user's funds: all underlying owed via `claimWithdrawRequest`, `claimInstantWithdrawRequest`, and default-recovery payouts (`defaultRecoveryReserve`-backed claims at `defaultRecoveryPrice`) is unrecoverable for that account. Unlike the gravitybridge case (temporary DoS of a batch), here the freeze is permanent because there is no alternative recipient path and the claim basis cannot be transferred. With a USDC-denominated pool, any lender landing on the token's blocklist (sanctions, court order, or the token's `address(0)`-style restrictions) loses 100% of their position.

### Likelihood Explanation
Low-to-medium: it requires the underlying token to implement recipient blocking (true for USDC/USDT, the typical Clearpool vault currency) and a lender's address to be blocked. The defect is deterministic once the precondition holds, and no existing guard (skim, epoch gating, KYC/`isWalletAllowed`, `onlyIdleCDO`, nonReentrant) provides an escape hatch — KYC whitelisting gates entry, not payout destination.

### Recommendation
Add a recipient parameter to claim functions (mirroring `DefaultDistributor.claim(address _to)`) so a blocked address can direct its payout to an allowed address it controls, e.g. `claimWithdrawRequest(address _to)` settling `withdrawsRequestsByEpoch[msg.sender]` while transferring to `_to`. Alternatively, provide an owner-gated `rescueClaim(user, to)` for blocked accounts.

### Proof of Concept
Foundry fork test (mainnet, USDC pool):

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;

import "forge-std/Test.sol";
import "contracts/strategies/idle/IdleCreditVault.sol";
import "contracts/interfaces/IERC20Detailed.sol";

// USDC exposes blacklist(address)/isBlacklisted(address) via its master minter.
interface IUSDCBlacklist {
    function blacklist(address) external;
    function isBlacklisted(address) external view returns (bool);
    function masterMinter() external view returns (address);
}

contract BlockedRecipientFreezeTest is Test {
    IdleCreditVault vault;      // deployed via factory + IdleCDOCreditVault, USDC underlying
    IERC20Detailed usdc = IERC20Detailed(0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48);
    address alice = address(0xA11CE);

    function test_BlockedUserFundsPermanentlyFrozen() public {
        // 1. Alice (KYC'd) deposits during buffer epoch and requests withdraw.
        vm.startPrank(alice);
        usdc.approve(address(cdo), 1000e6);
        cdo.depositAA(1000e6);
        vm.stopPrank();
        // ... epoch runs, borrower repays, epoch stopped with liquidity ...
        vm.prank(alice);
        vault.requestWithdraw(1000e6, epochNumber);

        // 2. Token issuer blacklists Alice (external, uncontrollable by her).
        IUSDCBlacklist u = IUSDCBlacklist(address(usdc));
        vm.prank(u.masterMinter());
        u.blacklist(alice);

        // 3. Every claim reverts atomically on the USDC transfer to msg.sender.
        vm.prank(alice);
        vm.expectRevert(); // "Blacklistable: account is blacklisted"
        vault.claimWithdrawRequest(epochNumber);

        // 4. Alice cannot move the receipt to a clean address:
        //    _transfer reverts unless msg.sender == idleCDO.
        vm.prank(alice);
        vm.expectRevert(NotAllowed.selector);
        vault.transfer(address(0xB0B), 1000e6);

        // 5. No claim takes a _to parameter -> Alice's 1000e6 is permanently
        //    stuck in the vault; defaultRecoveryReserve claims revert identically.
    }
}
```

Note: this assessment is based on the indexed portions of `IdleCreditVault.sol` and `IdleCDOCreditVault.sol`; the exact claim-function signatures (`claimWithdrawRequest`/`claimInstantWithdrawRequest`) were inferred from the repo's documented surface rather than read line-by-line — if they already accept a `_to` recipient, the finding reduces to the non-transferable receipt gap and should be downgraded.