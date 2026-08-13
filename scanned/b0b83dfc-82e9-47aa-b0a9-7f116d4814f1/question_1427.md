# Q1427: kamino_withdraw: withdraw targets the wrong recipient or vault authority [a-withdraw-amount-at-last] [recipient-binding]

## Question
Can an unprivileged attacker use `kamino_withdraw` with a withdraw amount at last-share and tiny redemption boundaries so `kamino_withdraw` sends withdrawn assets to the wrong recipient or through the wrong vault authority, breaking `Kamino withdraw must release only value actually redeemed from the exact obligation/reserve pair and correct recipient` and causing `Critical: direct theft or phantom redemption`? Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/withdraw.rs` / `kamino_withdraw`
- Entrypoint: `kamino_withdraw`
- Attacker controls: a withdraw amount at last-share and tiny redemption boundaries
- Exploit idea: Probe destination binding and PDA authority checks across the final transfer-out phase. Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.
- Invariant to test: Kamino withdraw must release only value actually redeemed from the exact obligation/reserve pair and correct recipient
- Expected Immunefi impact: Critical: direct theft or phantom redemption
- Fast validation: Swap destinations and authorities in the controlled setup and assert no accepted path transfers value to an unvalidated account. Swap every plausible destination and assert no accepted path releases value to an unvalidated recipient.
