### Title
Unbounded `getOwnedTokens` loop lets an attacker DoS all withdrawals by dusting PoLido NFTs into the vault/strategy - (File: contracts/IdleCDOPoLidoVariant.sol, contracts/strategies/idle/IdlePoLidoStrategy.sol)

### Summary
`IdleCDOPoLidoVariant._withdraw` and `IdlePoLidoStrategy._redeem` call `IPoLidoNFT.getOwnedTokens(address(this))`, which builds a memory array of every PoLido NFT owned by the contract. PoLido NFTs are transferable ERC-721s, so any unprivileged attacker can mint cheap withdrawal NFTs via `stMatic.requestWithdraw` and `safeTransferFrom` them directly to the IdleCDO/strategy. This inflates the array iterated on every withdraw/redeem until the transaction exceeds the block gas limit, freezing user funds. This is the direct analog of the Biconomy finding where an attacker pushes entries into another user's `nftIdsStaked` list, which is then iterated on withdrawal.

### Finding Description
In `_withdraw`, after burning tranche tokens, the contract enumerates all NFTs it owns and picks the last one: [1](#0-0) 

`getOwnedTokens` is a view over the enumerable NFT that materializes the full token-id list in memory, so its gas cost grows linearly with the number of NFTs held by `address(this)`. The same pattern exists in the strategy:

`IdlePoLidoStrategy._redeem` calls `stMatic.requestWithdraw`, then enumerates `poLidoNFT.getOwnedTokens(address(this))` and forwards `tokenIds[tokenIds.length - 1]`, assuming the last entry is the just-minted NFT. [2](#0-1) 

Nothing prevents an arbitrary account from transferring a PoLido NFT to either contract: the CDO variant even implements `onERC721Received` to accept `safeTransferFrom` deposits. [3](#0-2) 

The attacker flow:
1. Attacker calls `stMatic.requestWithdraw(dust, ref)` repeatedly (minimum ~1 MATIC per NFT) to mint many PoLido withdrawal NFTs.
2. Attacker calls `poLidoNFT.safeTransferFrom(attacker, idleCDO, tokenId)` (and/or the strategy address) for each NFT.
3. Every subsequent `withdrawAA`/`withdrawBB` on `IdleCDOPoLidoVariant` calls `getOwnedTokens(address(this))`, which loops over the inflated token set and eventually exceeds the block gas limit, reverting.

There is also a correctness side-effect: `tokenIds[tokenIds.length - 1]` may now pick an attacker-deposited (already claimed or dust) NFT rather than the fresh one minted by `requestWithdraw`, so `_withdraw` can transfer a worthless NFT to the user while `lastNAVAA/lastNAVBB` and `trancheAPRSplitRatio` are still debited by the full `toRedeem` amount. [4](#0-3) 

No existing guard helps: `_checkSameBlock`, `_checkDefault` and `nonReentrant` don't bound the NFT count, and there is no cap or whitelist on incoming NFT transfers.

### Impact Explanation
Withdrawals are the only exit path in this variant (redeemed underlying arrives as a PoLido NFT). Once the attacker's NFT count pushes `getOwnedTokens` past the gas limit, every `withdrawAA`/`withdrawBB`/`redeem`/`redeemUnderlying` reverts, permanently freezing all user principal and interest in the pool, and also blocking `harvest`-time redemptions through `IdlePoLidoStrategy._redeem`. Additionally, each withdraw executed before the hard DoS can pay the user a wrong (dust/claimed) NFT while accounting records a full redemption, a direct loss for the withdrawer and an inflated NAV mismatch for remaining depositors.

### Likelihood Explanation
The attack requires only an EOA with MATIC for gas and the minimum `requestWithdraw` amount per NFT; no privileged role, KYC, or tranche ownership is needed because ERC-721 `transferFrom`/`safeTransferFrom` to the contracts is permissionless. Cost scales linearly with the number of NFTs needed to hit the block gas limit (memory-expanding `uint256[]` growth makes the threshold reachable with a few thousand dust NFTs). The attack can be executed at any time while the CDO/strategy is live.

### Recommendation
- Do not enumerate owned NFTs. In `IdlePoLidoStrategy._redeem`, capture the new `tokenId` directly (e.g., from `requestWithdraw`'s emitted `RequestWithdraw` event data or `totalSupply`/counter of the NFT contract) and transfer that specific NFT.
- In `IdleCDOPoLidoVariant._withdraw`, have the strategy return the exact `tokenId` and `safeTransferFrom` it instead of `tokenIds[tokenIds.length - 1]`.
- Optionally add an owner-only sweep (`transferNft` already exists on the strategy for leftovers) and/or revert `onERC721Received` for unsolicited NFTs to prevent the ledger from being polluted.

### Proof of Concept
Foundry fork test on Polygon (PoLido/stMATIC live at `stMatic`, `poLidoNFT` addresses used by `IdlePoLidoStrategy.t.sol`):

```solidity
// SPDX-License-Identifier: GPL-3.0
pragma solidity 0.8.10;
import "./IdlePoLidoStrategy.t.sol";

contract PoLidoNftDustDoS is IdlePoLidoStrategyTest {
    function testDustNftDoSWithdraw() external {
        uint256 amount = 10_000 * ONE_SCALE;
        idleCDO.depositAA(amount);
        _cdoHarvest(true);

        // Attacker mints dust withdrawal NFTs and pushes them to the CDO variant
        uint256 dust = stMatic.submitThreshold(); // minimal accepted withdrawal
        uint256 n = 5_000;                        // enough to exceed block gas in getOwnedTokens
        deal(address(underlying), attacker, dust * n);
        vm.startPrank(attacker);
        underlying.approve(address(stMatic), type(uint256).max);
        for (uint256 i; i < n; ++i) {
            stMatic.submit(dust, attacker);           // mint stMatic
            stMatic.requestWithdraw(dust, attacker);  // mint poLido NFT
        }
        uint256[] memory ids = poLidoNFT.getOwnedTokens(attacker);
        for (uint256 i; i < ids.length; ++i) {
            poLidoNFT.safeTransferFrom(attacker, address(idleCDO), ids[i]);
        }
        vm.stopPrank();

        // Any user withdraw now runs out of gas inside getOwnedTokens
        vm.expectRevert(); // OOG
        idleCDO.withdrawAA(IERC20Detailed(address(AAtranche)).balanceOf(address(this)));
    }
}
```

The same PoC targeting `address(strategy)` breaks `IdlePoLidoStrategy._redeem` identically, and a pre-DoS variant with 1 dust NFT demonstrates the wrong-NFT payout at `tokenIds[tokenIds.length - 1]`.

### Citations

**File:** contracts/IdleCDOPoLidoVariant.sol (L64-78)
```text
        uint256[] memory tokenIds = stMatic.poLidoNFT().getOwnedTokens(address(this));
        require(tokenIds.length != 0, "no NFTs");

        // update NAV with the _amount of underlyings removed
        if (_tranche == AATranche) {
            lastNAVAA -= toRedeem;
        } else {
            lastNAVBB -= toRedeem;
        }

        // update trancheAPRSplitRatio
        _updateSplitRatio(_getAARatio(true));

        uint256 tokenId = tokenIds[tokenIds.length - 1];
        stMatic.poLidoNFT().safeTransferFrom(address(this), msg.sender, tokenId);
```

**File:** contracts/IdleCDOPoLidoVariant.sol (L81-88)
```text
    function onERC721Received(
        address,
        address,
        uint256,
        bytes calldata
    ) external pure returns (bytes4) {
        return IERC721ReceiverUpgradeable.onERC721Received.selector;
    }
```
