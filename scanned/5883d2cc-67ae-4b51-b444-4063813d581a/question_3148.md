# Q3148: lending_pool_configure_bank_oracle: staked-oracle transition can be used to brick live collateral paths [replay-of-a-previously-valid] [identity-vs-shape]

## Question
Can an unprivileged attacker invoke `lending_pool_configure_bank_oracle` with replay of a previously valid config shape under a new signer so `lending_pool_configure_bank_oracle` performs a staked-oracle transition that bricks or misprices live collateral paths, violating `oracle configuration changes must require exact authority and bind to the exact bank/oracle lineage intended` and causing `Critical: attacker-installed pricing enabling theft or insolvency`? Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/config_bank_oracle.rs` / `lending_pool_configure_bank_oracle`
- Entrypoint: `lending_pool_configure_bank_oracle`
- Attacker controls: replay of a previously valid config shape under a new signer
- Exploit idea: This is in scope when caused by a public bypass, not by an admin making a policy choice. Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.
- Invariant to test: oracle configuration changes must require exact authority and bind to the exact bank/oracle lineage intended
- Expected Immunefi impact: Critical: attacker-installed pricing enabling theft or insolvency
- Fast validation: Exercise the transition under attacker-controlled auth/binding attempts and assert it cannot affect live banks without the intended role and full validation. Substitute same-type oracle-related accounts and assert every non-canonical identity is rejected even if shape-compatible.
