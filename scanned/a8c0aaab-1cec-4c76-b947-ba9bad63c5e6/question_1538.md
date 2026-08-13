# Q1538: cpi_transfer_obligation_owner_to_destination: withdraw releases more value than the external position burned [attacker-controlled-destinations-alongside-canonical] [round-trip]

## Question
Can an unprivileged attacker call `kamino_withdraw` with attacker-controlled destinations alongside canonical-looking recipients so `cpi_transfer_obligation_owner_to_destination` releases more value than the corresponding external position actually burned, violating `final transfer-out after Kamino redemption must be bound to the canonical recipient and cannot replay or redirect value` and causing `Critical: direct theft of withdrawn assets`? Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/withdraw.rs` / `cpi_transfer_obligation_owner_to_destination`
- Entrypoint: `kamino_withdraw`
- Attacker controls: attacker-controlled destinations alongside canonical-looking recipients
- Exploit idea: Audit redeem/share conversions, rounding, and cached external balances around withdraw flows. Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.
- Invariant to test: final transfer-out after Kamino redemption must be bound to the canonical recipient and cannot replay or redirect value
- Expected Immunefi impact: Critical: direct theft of withdrawn assets
- Fast validation: Compare external burned shares/assets to internal released value under adversarial amounts and assert no excess release occurs. Run deposit-then-withdraw and withdraw-then-deposit loops and assert no positive attacker value emerges.
