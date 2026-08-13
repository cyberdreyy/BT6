# Q1602: cpi_transfer_obligation_owner_to_destination: withdraw accepts attacker-shaped optional accounts at closeout [attacker-controlled-destinations-alongside-canonical] [round-trip]

## Question
Can an unprivileged attacker use `kamino_withdraw` with attacker-controlled destinations alongside canonical-looking recipients so `cpi_transfer_obligation_owner_to_destination` accepts attacker-shaped optional accounts during closeout, violating `final transfer-out after Kamino redemption must be bound to the canonical recipient and cannot replay or redirect value` and causing `Critical: direct theft of withdrawn assets`? Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/withdraw.rs` / `cpi_transfer_obligation_owner_to_destination`
- Entrypoint: `kamino_withdraw`
- Attacker controls: attacker-controlled destinations alongside canonical-looking recipients
- Exploit idea: Probe optional reward, mint, reserve, or destination accounts used only during withdraw and therefore easy to under-validate. Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.
- Invariant to test: final transfer-out after Kamino redemption must be bound to the canonical recipient and cannot replay or redirect value
- Expected Immunefi impact: Critical: direct theft of withdrawn assets
- Fast validation: Supply valid-looking optional accounts from another context and assert withdraw never succeeds against them. Run deposit-then-withdraw and withdraw-then-deposit loops and assert no positive attacker value emerges.
