# Q3296: Dst creation timing can one-side the swap zksync_equal next_block

## Question
Can an unprivileged destination-escrow creator set destination timelocks so that `DstCancellation` is equal on zkSync while the source side uses EVM timing` while settlement reaches in the block right after the first destination action, leaving too little time for a valid destination withdrawal and causing the source and destination lifecycles to diverge into a frozen or one-sided outcome?

## Target
- File/function: `contracts/BaseEscrowFactory.sol::createDstEscrow`
- Entrypoint: `BaseEscrowFactory.createDstEscrow(IBaseEscrow.Immutables,uint256)`
- Attacker controls: all destination timelock values, the supplied `srcCancellationTimestamp`, and the timing of destination creation
- Exploit idea: Exploit the fact that `createDstEscrow()` only bounds the destination cancellation start against a raw source timestamp.
- Invariant to test: Creating a destination escrow should leave enough live time for a valid withdraw path before either side's cancel path dominates.
- Expected Immunefi impact: Temporary freezing of funds
- Fast validation: Deploy a destination escrow whose cancellation timing is equal on zkSync while the source side uses EVM timing, then drive settlement into in the block right after the first destination action and inspect whether a valid withdrawal remains possible on both chains.
