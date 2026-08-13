# Q2432: cpi_transfer_withdraw_intermediary_ata_to_destination: withdraw round-trip with deposit leaks value across the integration boundary [boundary-sized-withdrawals-near-one] [round-trip]

## Question
Can an unprivileged attacker cycle `juplend_withdraw` with boundary-sized withdrawals near one-unit residual transfers so `cpi_transfer_withdraw_intermediary_ata_to_destination` leaks value when combined with the matching deposit path, violating `intermediary ATA closeout after Juplend withdraw must be single-use, canonical, and non-redirectable` and causing `Critical: direct theft of redeemed assets`? Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/withdraw.rs` / `cpi_transfer_withdraw_intermediary_ata_to_destination`
- Entrypoint: `juplend_withdraw`
- Attacker controls: boundary-sized withdrawals near one-unit residual transfers
- Exploit idea: Look for asymmetric conversions or fees where deposit and withdraw are not true economic inverses around edge amounts. Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.
- Invariant to test: intermediary ATA closeout after Juplend withdraw must be single-use, canonical, and non-redirectable
- Expected Immunefi impact: Critical: direct theft of redeemed assets
- Fast validation: Run deposit-then-withdraw and withdraw-then-deposit cycles near boundaries and assert no cycle creates positive attacker value. Run deposit-then-withdraw and withdraw-then-deposit loops and assert no positive attacker value emerges.
