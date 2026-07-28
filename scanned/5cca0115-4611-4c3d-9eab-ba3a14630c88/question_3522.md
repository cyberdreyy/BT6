# Q3522: Dst creation can one-side funding erc20_exact minus_1

## Question
Can an unprivileged destination-escrow creator use a configuration where ERC20 destination with exact `msg.value = safetyDeposit` and destination cancellation one second below the source value, causing `createDstEscrow()` to accept funding that is formally sufficient for deployment but leaves too little live value or time for a valid destination withdrawal, producing a one-sided or frozen swap?

## Target
- File/function: `contracts/BaseEscrowFactory.sol::createDstEscrow`
- Entrypoint: `BaseEscrowFactory.createDstEscrow(IBaseEscrow.Immutables,uint256)`
- Attacker controls: the destination token mode, `msg.value`, all destination timelocks, and the supplied `srcCancellationTimestamp`
- Exploit idea: Probe the narrow funding and timing checks in `createDstEscrow()` for accepted-but-bad destination escrows.
- Invariant to test: A destination escrow that passes `createDstEscrow()` must still preserve enough value and time for a valid live settlement.
- Expected Immunefi impact: Temporary freezing of funds
- Fast validation: Create destination escrows where ERC20 destination with exact `msg.value = safetyDeposit` and destination cancellation one second below the source value, then drive the live flow and inspect whether valid withdrawal can still complete.
