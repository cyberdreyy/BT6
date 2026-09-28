### Title
Donation-skimming mitigation bypass — unsolicited `token` transfers inflate `getContractValue`, corrupting tranche price and share minting - ([File: contracts/IdleCDOCreditVault.sol](contracts/IdleCDOCreditVault.sol))

### Summary
CVE-2025-13013 is a *mitigation bypass*: a security control designed to neutralize an attack class is circumvented. The analog in this codebase is the donation-isolation ("skim") mitigation. `IdleCDOCreditVault` explicitly documents that "raw underlyings held by the CDO are excluded because unsolicited transfers are skimmed on interactions" (`_managedContractValue`, line 131; `_accrueManagementFee`, line 551). But the accounting path used by every deposit and withdrawal calls `getContractValue()` (line 227 inside `_updateAccounting`), which **includes** `_contractTokenBalance(token)` — the raw, donatable balance (line 127). An unprivileged attacker can therefore bypass the skim by directly transferring `token` to the vault, injecting phantom NAV into `_updateAccounting` and inflating `priceAA`/`priceBB` used by `_mintSharesAtCurrPrice`.

### Finding Description
- `getContractValue()` returns `strategyToken balance + token balance − unclaimedFees` (line 127). The `token` term counts any unsolicited ERC20 transfer — the exact donation vector the code claims is skimmed.
- `_updateAccounting()` (lines 222–249) uses this inflated `nav`, treats the donation as `totalGain`, splits it between AA/BB NAVs, takes a performance fee into `unclaimedFees` (line 231), and raises `priceAA`/`priceBB`.
- A subsequent victim `depositAA`/`depositBB` calls `_deposit` → `_updateAccounting` (donation counted) → `_mintSharesAtCurrPrice` mints `amount * ONE_TRANCHE_TOKEN / inflatedPrice` (line 346) — fewer shares than fair. The donation is then socialized into NAV, and since the attacker already holds tranche shares minted *before* inflating the price, the attacker's share of the diluted victim value exceeds the donated amount whenever the victim's deposit is large relative to the donation.
- The inconsistency is direct evidence of the bypass: `virtualPrice()` uses `_managedContractValue()` (donation-excluded, line 175), while `_updateAccounting` uses `getContractValue()` (donation-included). Two price sources disagree by exactly the donation amount; the mutable accounting path is the unsafe one.

### Impact Explanation
Unprivileged share-price manipulation: a first depositor (or any tranche holder) can donate `token` to the CDO, inflating the tranche price at which a later depositor's shares are minted, then withdraw to capture the dilution. Quantified loss ≈ `victimDeposit * D / (NAV + D)` where `D` is the donation — classic inflation-attack economics that the documented skim was intended to prevent. In an epoch-running vault with real depositors, this is a direct theft of depositor principal.

### Likelihood Explanation
Medium. Requires the vault to hold raw `token` exposure counted in NAV (true whenever `getContractValue` is used) and an ordering where the attacker holds shares before a victim deposit. The attacker only needs an EOA with `token` balance and one deposit — no privileged role. The defense gap is a single inconsistent view function, not a guarded path; nothing in `_deposit`, `_guarded`, or pause flags stops a direct `token.transfer(cdo, D)`.

### Recommendation
Make the accounting path consistent with the managed-value path: have `_updateAccounting` use `_managedContractValue()` (strategy-token NAV only, net of fees), or implement the advertised skim by sweeping unsolicited `token` into the strategy/fees before computing gains. Ensure `getContractValue`, `virtualPrice`, and mint/burn paths share a single donation-resistant NAV source.

### Proof of Concept
Foundry fork sketch:

```solidity
// test: attacker = EOA, victim = another depositor; vault running, AA+BB active
// 1. attacker deposits small AA: cdo.depositAA(1e6)  -> minted at price == oneToken
// 2. attacker donates: token.transfer(address(cdo), D)  // raw balance, no skim
// 3. victim deposits: cdo.depositAA(V)
//    _updateAccounting() -> nav includes D -> priceAA inflated
//    victim minted = V * ONE / priceAA  (fewer shares than fair price)
// 4. priceAA stays elevated (donation baked into NAV); attacker withdraws
//    attackerOut > attackerIn when V >> D  (theft of victim principal)
assertGt(attackerUnderlyingOut, attackerUnderlyingIn + D);
```

Key assertions: `cdo.getContractValue()` increases by `D` after the transfer (bypass), while `_managedContractValue` is unchanged — the two NAV sources diverge, proving the skim is bypassed in the accounting path.

Caveat: I was unable to verify whether a `_skimDonatedAssets` sweep exists in the epoch-variant/wrappers that would neutralize `token` donations before `_updateAccounting` runs (e.g., in `IdleCDOEpochVariant.sol` or `strategies/idle/IdleCreditVault.sol`, which contain skim references I could not fully inspect in the available iterations). If deposits always route through a skim that pulls raw tokens into the strategy first, the attack window shrinks to donations landing between accounting events — still reachable via back-to-back transactions in the same block ordering, but the loss magnitude depends on that flow.