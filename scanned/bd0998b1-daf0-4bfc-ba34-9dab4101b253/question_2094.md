# Q2094: Src rescue can drain maker principal cross_chain_race partial_50

## Question
If a source escrow for a partial fill around the 50% band remains funded because the destination public withdrawal revealed the secret too late for source-side recovery, can the taker wait until `RESCUE_DELAY` and call `rescueFunds(immutables.token, immutables.amount, immutables)` to transfer the maker's original source tokens to themselves, even though the documentation says rescue is only for accidentally stuck assets?

## Target
- File/function: `contracts/BaseEscrow.sol::rescueFunds`, `contracts/EscrowSrc.sol`
- Entrypoint: `BaseEscrow.rescueFunds(address,uint256,IBaseEscrow.Immutables)` on an `EscrowSrc` clone
- Attacker controls: the taker address encoded in the escrow, rescue timing after `RESCUE_DELAY`, and the choice of `token=immutables.token`, `amount=immutables.amount`
- Exploit idea: Use `rescueFunds()` itself to seize the live maker principal after the public windows lapse.
- Invariant to test: Rescue on the source side must never let the taker seize the original maker principal that the normal cancel path should return to the maker.
- Expected Immunefi impact: Direct theft of any user funds, whether at-rest or in-motion
- Fast validation: Create a source escrow for a partial fill around the 50% band, leave it funded until `RESCUE_DELAY` under the condition that the destination public withdrawal revealed the secret too late for source-side recovery, then call `rescueFunds` for the full maker asset and check whether the taker receives it.
