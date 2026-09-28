### Title
Unprivileged NFT transfer corrupts `getOwnedTokens` ordering — freshly minted PoLido withdrawal NFTs get stranded in `IdlePoLidoStrategy`/`IdleCDOPoLidoVariant` and victims receive attacker-supplied dust NFTs - (File: contracts/strategies/lido/IdlePoLidoStrategy.sol, contracts/IdleCDOPoLidoVariant.sol)

### Summary
The Arcadia bug class — a newly minted NFT position whose real `tokenId` is never tracked, so it stays locked in an intermediary contract — has a direct analog in the PoLido integration. Both `IdlePoLidoStrategy._redeem` and `IdleCDOPoLidoVariant._withdraw` never learn the ID of the NFT that `stMatic.requestWithdraw` just minted; instead they guess it as `getOwnedTokens(address(this))[length - 1]`. Because `getOwnedTokens` (ERC721Enumerable-style) appends incoming tokens to the end of the owner's list, an unprivileged attacker can `safeTransferFrom` a dust-value PoLido withdrawal NFT to the strategy or CDO right before a victim's withdrawal, forcing the victim's tranche burn to be paid out with the attacker's NFT while the victim's real withdrawal NFT (worth the full redeemed MATIC) remains stranded in the intermediary contract.

### Finding Description
In `IdlePoLidoStrategy._redeem` (contracts/strategies/lido/IdlePoLidoStrategy.sol:121-138):

```solidity
stMatic.requestWithdraw(_amount, TREASURY);
IPoLidoNFT poLidoNFT = stMatic.poLidoNFT();
uint256[] memory tokenIds = poLidoNFT.getOwnedTokens(address(this));
// NOTE: assume last token is the one we just minted.
uint256 tokenId = tokenIds[tokenIds.length - 1];
poLidoNFT.safeTransferFrom(address(this), msg.sender, tokenId);
_redeemed = stMatic.getMaticFromTokenId(tokenId);
```

The code explicitly assumes the last element of `getOwnedTokens` is the just-minted NFT. That is only true if nobody else has pushed a token to the end of the list. `IPoLidoNFT.getOwnedTokens` (contracts/interfaces/IPoLidoNFT.sol:10) enumerates owned token IDs, and in enumerable ERC721 implementations a `transferFrom`/`safeTransferFrom` to an address appends the token to the end of that address's list — the same position where a fresh mint lands.

The same pattern repeats in `IdleCDOPoLidoVariant._withdraw` (contracts/IdleCDOPoLidoVariant.sol:64-78), which takes `tokenIds[tokenIds.length - 1]` of the CDO's own holdings and forwards it to `msg.sender`.

Attack sequence (attacker = unprivileged "direct token sender", fully within the allowed threat model):

1. Attacker calls `stMatic.requestWithdraw(1 wei, ref)` to mint themselves a dust-value PoLido NFT (tokenId X, worth ~1 wei of MATIC).
2. Victim calls `idleCDO.withdrawAA(amount)`. Inside `_withdraw`, the strategy calls `stMatic.requestWithdraw`, minting NFT Y worth the victim's full `toRedeem`.
3. In the same block (or via general frontrunning, since `withdrawAA` is a public mempool transaction), attacker calls `poLidoNFT.safeTransferFrom(attacker, address(strategy), X)`. The strategy's owned list becomes `[Y, X]` — X is now last.
4. `IdlePoLidoStrategy._redeem` picks `tokenIds[length-1] = X`, transfers the dust NFT X to the CDO and returns `_redeemed = getMaticFromTokenId(X)` ≈ 0.
5. The CDO then takes its own `tokenIds[length-1]` — X — and sends it to the victim (IdleCDOPoLidoVariant.sol:77-78). The victim's tranche tokens were burned (line 60), NAV was decremented, but they hold a ~zero-value claim NFT. NFT Y, the real withdrawal claim, stays owned by the strategy.

The same dusting works directly against the CDO: transferring X to `IdleCDOPoLidoVariant` makes the CDO forward X to `msg.sender` even when the strategy correctly delivered Y to the CDO (Y lands at `length-2`).

The broken invariant is "one receipt, one payout": the protocol burns the user's tranche tokens and reduces `lastNAVAA/BB`, but delivers an unrelated, attacker-chosen receipt. The minted position that encodes the real claim is locked in the intermediary — recoverable only via the honest owner's `transferNft` rescue (IdlePoLidoStrategy.sol:190), not by the user, exactly mirroring the Arcadia issue where the NFT stays in the Action Handler.

No existing guard stops this: `_checkSameBlock` only blocks deposit-then-withdraw by the same user in one block, not a third-party NFT transfer; `revertIfTooLow` in `_liquidate` compares against `toRedeem` returned by the strategy itself, which is already computed from the poisoned tokenId, so it does not detect the substitution (verify `revertIfTooLow` semantics in `IdleCDO._liquidate` — if it enforces a minimum vs. the pre-computed `toRedeem`, it may mitigate the strategy-side variant but cannot stop the CDO-side dusting, since the CDO forwards whatever is last in its own list unconditionally).

### Impact Explanation
High. The victim's tranche tokens are burned and the tranche NAV is reduced, yet the victim receives a dust-value withdrawal NFT. Their actual claim (the full redeemed MATIC amount) is stranded in `IdlePoLidoStrategy` (or swapped at the CDO layer). At best this is a temporary/permanent freeze of the victim's funds pending owner rescue; at worst, if combined with NAV desync (`lastNAVAA -= toRedeem` using the dust NFT's `getMaticFromTokenId` value vs. the real claim value), it corrupts tranche pricing for all other holders. Repeatable on every withdrawal as long as the attacker holds or can cheaply mint dust PoLido NFTs.

### Likelihood Explanation
Medium-high whenever the variant is active. Requirements are minimal: the attacker needs any PoLido NFT (mintable via `stMatic.requestWithdraw` with a dust amount — permissionless) and the ability to `safeTransferFrom` it to a public contract address. Withdrawals are visible in the mempool, so the ordering attack is straightforward; even non-targeted "griefing" dust permanently left in the strategy/CDO corrupts the very next withdrawal.

### Recommendation
Do not infer the minted token ID from list position. `requestWithdraw` should be wrapped so the actual minted `tokenId` is captured (e.g., diff `balanceOf`/track `tokenIds` length before vs. after, or read stMATIC's request counter/events). Also:

- In `IdleCDOPoLidoVariant._withdraw`, forward the exact `tokenId` returned by the strategy's `redeemUnderlying` rather than re-scanning the CDO's own holdings.
- Consider rejecting stray NFTs: keep a counter of expected incoming mints vs. `poLidoNFT.balanceOf(this)` and revert if extra unsolicited NFTs exist, or pull the just-minted ID explicitly so unsolicited transfers cannot shift ordering.

### Proof of Concept
Foundry fork PoC sketch (Ethereum mainnet fork; `test/foundry/IdlePoLidoStrategy.t.sol` already provides the deployment harness and real `stMatic`/`poLidoNFT` addresses):

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.10;
import "./IdlePoLidoStrategy.t.sol"; // reuse existing fork setup

contract PoLidoNftOrderingPoC is IdlePoLidoStrategyTest {
    function testDustNftStealsWithdrawal() external {
        uint256 amount = 10000 * ONE_SCALE;
        idleCDO.depositAA(amount);
        _cdoHarvest(true);
        skip(7 days); vm.roll(block.number + 1);

        // 1) Attacker mints dust withdrawal NFT
        address attacker = address(0xbad);
        deal(address(underlying), attacker, 1e18); // MATIC
        vm.startPrank(attacker);
        underlying.approve(address(stMatic), 1e18);
        stMatic.submit(1e18, attacker);
        stMatic.requestWithdraw(1, attacker); // dust claim NFT
        uint256[] memory atkIds = poLidoNFT.getOwnedTokens(attacker);
        uint256 dustId = atkIds[atkIds.length - 1];

        // 2) Victim withdrawal in mempool -> attacker frontruns dust NFT into strategy
        //    (or into the CDO for the second-layer variant)
        poLidoNFT.safeTransferFrom(attacker, address(strategy), dustId);
        vm.stopPrank();

        // 3) Victim withdraws: receives dustId, real claim NFT stays in strategy
        idleCDO.withdrawAA(IERC20Detailed(address(AAtranche)).balanceOf(address(this)));

        assertEq(poLidoNFT.ownerOf(dustId), address(this), "victim got dust NFT");
        assertGt(poLidoNFT.balanceOf(address(strategy)), 0, "real NFT stranded in strategy");
        assertLt(stMatic.getMaticFromTokenId(dustId), amount, "claim is worthless");
    }
}
```

Caveat on verification: I confirmed the `getOwnedTokens(...)[length-1]` assumption and the missing ID tracking in both files, and that `transferNft`/`transferToken` owner-rescue exists (meaning funds are not unrecoverable by the honest owner). I could not fully verify (a) the exact enumeration ordering of the deployed PoLidoNFT contract — the PoC assumes standard append-on-transfer enumeration — and (b) whether `revertIfTooLow` inside `_liquidate` enforces an absolute minimum that would revert the strategy-side path; the CDO-side dusting (step into `IdleCDOPoLidoVariant` directly) bypasses any such check regardless. If the PoLido variant is classified as a legacy/out-of-scope strategy for this engagement, the finding should be downgraded accordingly — the credit-vault (IdleCreditVault) surface has no NFT-minted-position equivalent, so no analog exists there.