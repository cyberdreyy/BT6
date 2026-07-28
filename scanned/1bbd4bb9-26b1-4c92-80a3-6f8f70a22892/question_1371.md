# Q1371: Src publicWithdraw replay after native top-up same_block_replay dust

## Question
Once `SrcPublicWithdrawal` is open, can an access-token holder replay `EscrowSrc.publicWithdraw()` at in the same block as the first lifecycle call after a later native-token top-up of `1 wei`, extracting another fixed `safetyDeposit` from the same clone because the public withdraw path never becomes terminal?

## Target
- File/function: `contracts/EscrowSrc.sol::publicWithdraw`
- Entrypoint: `EscrowSrc.publicWithdraw(bytes32,IBaseEscrow.Immutables)`
- Attacker controls: the public secret, access-token ownership, replay timing, and later native-token balances sent to the clone
- Exploit idea: Use the public withdraw path as a second native-refund drain after the first successful execution.
- Invariant to test: A source escrow must not expose more than one public or private safety-deposit refund for the same withdrawal.
- Expected Immunefi impact: Theft of coins or tokens intended for transaction fees
- Fast validation: Public-withdraw once, send `1 wei` of native token to the source clone before `SrcCancellation`, then call `publicWithdraw()` again and check whether the second refund succeeds.
