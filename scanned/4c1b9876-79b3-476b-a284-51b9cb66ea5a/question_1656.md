# Q1656: cpi_transfer_obligation_owner_to_destination: withdraw round-trip with deposit leaks value across the integration boundary [duplicate-metas-that-alter-which] [round-trip]

## Question
Can an unprivileged attacker cycle `kamino_withdraw` with duplicate metas that alter which destination is interpreted as canonical so `cpi_transfer_obligation_owner_to_destination` leaks value when combined with the matching deposit path, violating `final transfer-out after Kamino redemption must be bound to the canonical recipient and cannot replay or redirect value` and causing `Critical: direct theft of withdrawn assets`? Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/withdraw.rs` / `cpi_transfer_obligation_owner_to_destination`
- Entrypoint: `kamino_withdraw`
- Attacker controls: duplicate metas that alter which destination is interpreted as canonical
- Exploit idea: Look for asymmetric conversions or fees where deposit and withdraw are not true economic inverses around edge amounts. Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.
- Invariant to test: final transfer-out after Kamino redemption must be bound to the canonical recipient and cannot replay or redirect value
- Expected Immunefi impact: Critical: direct theft of withdrawn assets
- Fast validation: Run deposit-then-withdraw and withdraw-then-deposit cycles near boundaries and assert no cycle creates positive attacker value. Run deposit-then-withdraw and withdraw-then-deposit loops and assert no positive attacker value emerges.
