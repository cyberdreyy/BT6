### Title
Hostname-matching bypass in AWS Nitro CRL allowlist enables trust-policy bypass of a permissionless attestation cert - ([File: crates/proof/tee/registrar/src/crl.rs])

### Summary
The `is_allowed_crl_host` check in the TEE registrar's CRL-checking logic uses a weak `ends_with(".amazonaws.com") && contains("nitro-enclave")` substring match to decide whether a CRL distribution-point URL extracted from an attacker-suppliable certificate is "trusted AWS Nitro" infrastructure. Because AWS S3 bucket-style subdomains under `*.amazonaws.com` are attacker-controllable, this substring-based host check can be satisfied by a bucket/host the attacker owns, letting the attacker serve a forged (empty) CRL response and bypass revocation checking — the same class of bug as the underlying CVE (inconsistent/insufficiently-strict hostname matching leading to a trust-policy bypass).

### Finding Description
`CertManager.verifyCACertWithHints` / `verifyClientCertWithHints` are explicitly documented and encoded as **permissionless** on-chain calls — any unprivileged transaction sender can submit an arbitrary DER certificate to be cached: [1](#0-0) .

The registrar extracts the CRL Distribution Point URI directly from attacker-supplied certificate DER without any origin restriction beyond a hostname check performed later: [2](#0-1) .

That hostname check is:
```
host.ends_with(".amazonaws.com") && host.contains("nitro-enclave")
``` [3](#0-2) 

Because arbitrary S3 buckets can be provisioned under `*.s3.<region>.amazonaws.com` (and `*.amazonaws.com` more broadly, e.g. CloudFront/API Gateway custom subdomains), an attacker can create a resource whose hostname both ends in `.amazonaws.com` and contains the substring `nitro-enclave` (e.g. `evil-nitro-enclave-crl.s3.amazonaws.com`) while not being the real AWS Nitro Enclave CRL endpoint. The check does not verify an exact/allow-listed FQDN or a proper AWS-service-name label boundary — it is a loose substring/suffix match, structurally the same class of hostname-matching inconsistency described in the CVE (a check that appears to enforce a trust boundary but can be satisfied by attacker-controlled infrastructure).

The revocation check is explicitly **fail-open**: any error fetching/parsing the CRL is only logged, not treated as revoked: [4](#0-3) . Combined with the weak host check, an attacker who registers a permissionless CA/leaf certificate whose CRL Distribution Point points to attacker-owned, allowlist-satisfying infrastructure can serve a syntactically-valid but always-empty CRL, so `check_chain_against_crls` never reports the certificate/serial as revoked — even if the corresponding real-world Nitro certificate has actually been revoked by AWS.

### Impact Explanation
The CRL check feeds the registrar's decision to trust and register a TEE signer's certificate chain (via `signer_manager.rs`, which calls `ensure_cert_cached` / `validate_cached_cert` against `CertManager`) [5](#0-4) . If the off-chain CRL revocation gate can be bypassed via a spoofable "allowed" host, a compromised or otherwise-revoked Nitro enclave identity could remain trusted as a registered TEE prover signer past its intended revocation, allowing that signer to keep participating in the TEE proving/attestation pipeline that underpins fault-proof/output-root trust — i.e., a path toward a wrong provable output root being accepted from an attacker-influenced or compromised enclave that should have been revoked.

### Likelihood Explanation
Reaching the vulnerable code path requires only a permissionless on-chain call (`verifyCACertWithHints`/`verifyClientCertWithHints`) that anyone can submit, plus control of an AWS-hosted resource (e.g., an S3 bucket) whose hostname satisfies the naive `ends_with`/`contains` pattern — something within reach of any attacker with an AWS account. No special privileges on the Base protocol are needed to plant the malicious CRL Distribution Point in a certificate.

### Recommendation
Replace the substring/suffix host check with a precise, canonicalized allowlist of exact hostnames (or a properly anchored domain-label match, e.g. verifying the host is exactly `*.s3.<region>.amazonaws.com` label-bounded, or better, pin the small, fixed set of real AWS Nitro CRL hostnames actually used by AWS) rather than `ends_with(suffix) && contains(keyword)`. Additionally, consider treating CRL fetch/parse failures for a *known-important* revocation lookup as fail-closed (or require the enclave chain to only be trusted once online revocation is confirmed) rather than fail-open, to reduce the blast radius if a host check is ever bypassed.

### Proof of Concept
1. Attacker crafts a Nitro-style X.509 certificate whose CRL Distribution Point extension contains a URI such as `http://my-nitro-enclave-decoy.s3.amazonaws.com/crl/attacker.crl`, which the attacker controls (a real, attacker-owned S3 bucket).
2. Attacker calls the permissionless `verifyCACertWithHints`/`verifyClientCertWithHints` on `CertManager` with this certificate and valid P-384 hint data (or otherwise causes the registrar to process this cert chain through the normal signer-registration flow).
3. The registrar extracts the CRL URL via `extract_crl_distribution_point` and calls `is_allowed_crl_host`, which returns `true` because the host ends with `.amazonaws.com` and contains `nitro-enclave`.
4. The registrar fetches the attacker's empty/forged CRL from the attacker-controlled bucket; `crl_contains_serial` finds no match, so `check_chain_against_crls` reports the certificate as not revoked, even though the real certificate/serial may be revoked by AWS's genuine CRL.
5. This lets the registrar consider a revoked (or attacker-related) certificate as valid, undermining the revocation trust boundary the check was meant to enforce.

Note: I could not fully trace, within the available tooling, the exact downstream consequence path from `check_chain_against_crls`'s result to `signer_manager`'s final accept/reject decision (e.g., whether a "not revoked" result from this specific fail-open CRL check alone is sufficient to register a signer, or whether it's only one of several redundant checks including on-chain P-384 signature verification and other attestation validations). This should be verified in a Devin session with full repository access before treating this as a confirmed, fully-exploitable end-to-end vulnerability.

### Citations

**File:** crates/proof/contracts/src/cert_manager.rs (L238-252)
```rust
/// Encodes a permissionless CA cache transaction using supplied signature hints.
pub fn encode_verify_ca_cert_with_hints_calldata(
    cert: Bytes,
    parent_cert_hash: B256,
    signature_hints: Bytes,
) -> Bytes {
    Bytes::from(
        ICertManager::verifyCACertWithHintsCall {
            cert,
            parentCertHash: parent_cert_hash,
            signatureHints: signature_hints,
        }
        .abi_encode(),
    )
}
```

**File:** crates/proof/tee/registrar/src/crl.rs (L111-130)
```rust
fn extract_crl_distribution_point(cert: &X509Certificate<'_>) -> Option<String> {
    for ext in cert.extensions() {
        let ParsedExtension::CRLDistributionPoints(cdp) = ext.parsed_extension() else {
            continue;
        };
        for dp in cdp.iter() {
            let Some(name) = &dp.distribution_point else { continue };
            let x509_parser::extensions::DistributionPointName::FullName(names) = name else {
                continue;
            };
            for gn in names {
                let GeneralName::URI(uri) = gn else { continue };
                if uri.starts_with("http://") || uri.starts_with("https://") {
                    return Some(uri.to_string());
                }
            }
        }
    }
    None
}
```

**File:** crates/proof/tee/registrar/src/crl.rs (L132-138)
```rust
fn is_allowed_crl_host(url: &str) -> bool {
    reqwest::Url::parse(url).is_ok_and(|u| {
        u.domain().is_some_and(|host| {
            host.ends_with(ALLOWED_CRL_HOST_SUFFIX) && host.contains(ALLOWED_CRL_HOST_KEYWORD)
        })
    })
}
```

**File:** crates/proof/tee/registrar/src/crl.rs (L140-191)
```rust
/// Checks intermediate certificates against their CRL distribution points.
///
/// **Fail-open policy**: CRL fetch or parse failures are logged as warnings
/// but do not abort the check. Only confirmed revocations are reported.
///
/// # Arguments
///
/// * `cert_infos` - Pre-parsed cert chain info, typically produced once per
///   cycle by [`CertCrlInfo::from_chain`] and shared with the onchain
///   revocation pre-check so the DER parse only happens once.
/// * `http_client` - HTTP client for fetching CRLs.
pub async fn check_chain_against_crls<'a>(
    cert_infos: &'a [CertCrlInfo],
    http_client: &reqwest::Client,
) -> Vec<&'a CertCrlInfo> {
    let mut revoked = Vec::new();

    for info in cert_infos {
        let Some(ref crl_url) = info.crl_url else {
            debug!(cert_index = info.index, "no CRL distribution point, skipping");
            continue;
        };

        debug!(cert_index = info.index, url = %crl_url, "fetching CRL");

        match fetch_and_check_crl(http_client, crl_url, &info.serial_number).await {
            Ok(true) => {
                warn!(
                    cert_index = info.index,
                    url = %crl_url,
                    serial = %hex::encode(&info.serial_number),
                    revocation_id = %info.revocation_id,
                    "certificate found on CRL — REVOKED"
                );
                revoked.push(info);
            }
            Ok(false) => {
                debug!(cert_index = info.index, "certificate not on CRL");
            }
            Err(e) => {
                warn!(
                    cert_index = info.index,
                    url = %crl_url,
                    error = %e,
                    "CRL check failed (fail-open, proceeding)"
                );
            }
        }
    }

    revoked
}
```

**File:** crates/proof/tee/registrar/src/signer_manager.rs (L723-758)
```rust
    async fn ensure_cert_cached(
        &self,
        cert: &CertPlan,
        signature_hints: &[u8],
        signer: Address,
        timestamp_ms: u64,
        signer_cancel: &CancellationToken,
    ) -> Result<bool> {
        let lock = self.cert_lock(cert.cert_hash);
        let Some(_guard) = signer_cancel.run_until_cancelled(lock.lock()).await else {
            return Ok(false);
        };

        match self.validate_cached_cert(cert, signer_cancel).await? {
            None => return Ok(false),
            Some(true) => {
                RegistrarMetrics::record_cache_lookup(cert.kind, true);
                return Ok(true);
            }
            Some(false) => RegistrarMetrics::record_cache_lookup(cert.kind, false),
        }

        let tx_data = match cert.kind {
            CertKind::Ca => encode_verify_ca_cert_with_hints_calldata(
                Bytes::copy_from_slice(&cert.cert),
                cert.parent_cert_hash,
                Bytes::copy_from_slice(signature_hints),
            ),
            CertKind::Leaf => encode_verify_client_cert_with_hints_calldata(
                Bytes::copy_from_slice(&cert.cert),
                cert.parent_cert_hash,
                Bytes::copy_from_slice(signature_hints),
            ),
        };
        let candidate =
            TxCandidate { tx_data, to: Some(self.cert_manager.address()), ..Default::default() };
```
