# Q1607: cpi_transfer_obligation_owner_to_destination: withdraw accepts attacker-shaped optional accounts at closeout [duplicate-metas-that-alter-which] [recipient-binding]

## Question
Can an unprivileged attacker use `kamino_withdraw` with duplicate metas that alter which destination is interpreted as canonical so `cpi_transfer_obligation_owner_to_destination` accepts attacker-shaped optional accounts during closeout, violating `final transfer-out after Kamino redemption must be bound to the canonical recipient and cannot replay or redirect value` and causing `Critical: direct theft of withdrawn assets`? Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/withdraw.rs` / `cpi_transfer_obligation_owner_to_destination`
- Entrypoint: `kamino_withdraw`
- Attacker controls: duplicate metas that alter which destination is interpreted as canonical
- Exploit idea: Probe optional reward, mint, reserve, or destination accounts used only during withdraw and therefore easy to under-validate. Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.
- Invariant to test: final transfer-out after Kamino redemption must be bound to the canonical recipient and cannot replay or redirect value
- Expected Immunefi impact: Critical: direct theft of withdrawn assets
- Fast validation: Supply valid-looking optional accounts from another context and assert withdraw never succeeds against them. Swap every plausible destination and assert no accepted path releases value to an unvalidated recipient.
