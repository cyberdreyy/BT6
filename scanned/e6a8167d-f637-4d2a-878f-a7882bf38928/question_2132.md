# Q2132: Src rescue can drain safety deposit stalled_resolver partial_10

## Question
If a source escrow for a partial fill around the 10% band is still holding its original native `safetyDeposit` because the resolver stayed inactive through the cancellation window, can the taker wait until `RESCUE_DELAY` and call `rescueFunds(address(0), immutables.safetyDeposit, immutables)` to pull that live safety deposit instead of letting the normal withdraw or cancel incentives decide who earns it?

## Target
- File/function: `contracts/BaseEscrow.sol::rescueFunds`, `contracts/EscrowSrc.sol`
- Entrypoint: `BaseEscrow.rescueFunds(address,uint256,IBaseEscrow.Immutables)` on an `EscrowSrc` clone
- Attacker controls: the taker address, rescue timing after `RESCUE_DELAY`, and the ability to request `address(0)` for the full original safety deposit
- Exploit idea: Turn source-side rescue into a delayed sweep of the original safety deposit.
- Invariant to test: Rescue should not let the taker bypass the intended incentive model for the original safety deposit.
- Expected Immunefi impact: Theft of coins or tokens intended for transaction fees
- Fast validation: Keep a live source escrow for a partial fill around the 10% band idle until `RESCUE_DELAY` while the resolver stayed inactive through the cancellation window, then call `rescueFunds(address(0), immutables.safetyDeposit, immutables)` and inspect who receives the native balance.
