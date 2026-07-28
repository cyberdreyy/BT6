# Q2534: Dst withdraw fee boundary can mispay fees_almost_all before_cancel

## Question
At one block before `DstCancellation`, can a malicious destination-escrow creator choose `parameters` so that fees sum to `immutables.amount - 1` and `EscrowDst.withdraw()` either underflows `amount`, overpays protocol or integrator recipients, or burns the maker payout, leading to direct theft, insolvency, or a stuck destination settlement?

## Target
- File/function: `contracts/EscrowDst.sol::withdraw`, `contracts/libraries/ImmutablesLib.sol`
- Entrypoint: `EscrowDst.withdraw(bytes32,IBaseEscrow.Immutables)`
- Attacker controls: the encoded protocol fee amount, integrator fee amount, both fee recipients, and the call timing inside the live window
- Exploit idea: Probe fee-boundary cases around `amount - protocolFee - integratorFee` in the private withdraw path.
- Invariant to test: Destination settlement must never pay more than was funded and must not silently destroy the maker payout.
- Expected Immunefi impact: Protocol insolvency
- Fast validation: Deploy a destination escrow where fees sum to `immutables.amount - 1`, open the withdrawal window at one block before `DstCancellation`, and inspect whether balances or payouts become inconsistent.
