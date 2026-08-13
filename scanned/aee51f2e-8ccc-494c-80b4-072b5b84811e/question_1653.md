# Q1653: cpi_transfer_obligation_owner_to_destination: withdraw round-trip with deposit leaks value across the integration boundary [a-final-transfer-after-partial] [recipient-binding]

## Question
Can an unprivileged attacker cycle `kamino_withdraw` with a final transfer after partial CPI success/failure conditions so `cpi_transfer_obligation_owner_to_destination` leaks value when combined with the matching deposit path, violating `final transfer-out after Kamino redemption must be bound to the canonical recipient and cannot replay or redirect value` and causing `Critical: direct theft of withdrawn assets`? Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/withdraw.rs` / `cpi_transfer_obligation_owner_to_destination`
- Entrypoint: `kamino_withdraw`
- Attacker controls: a final transfer after partial CPI success/failure conditions
- Exploit idea: Look for asymmetric conversions or fees where deposit and withdraw are not true economic inverses around edge amounts. Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.
- Invariant to test: final transfer-out after Kamino redemption must be bound to the canonical recipient and cannot replay or redirect value
- Expected Immunefi impact: Critical: direct theft of withdrawn assets
- Fast validation: Run deposit-then-withdraw and withdraw-then-deposit cycles near boundaries and assert no cycle creates positive attacker value. Swap every plausible destination and assert no accepted path releases value to an unvalidated recipient.
