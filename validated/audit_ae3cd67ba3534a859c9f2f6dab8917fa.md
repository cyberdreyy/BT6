### Title
Unbounded `io.ReadAll(resp.Body)` in `httpFetcher.Fetch` allows memory exhaustion via malicious/MITM'd AIA issuer-certificate endpoint - (File: helpers/tls/ca_chain/resolver_url.go)

### Summary
`httpFetcher.Fetch` reads the entire HTTP response body from the certificate's `IssuingCertificateURL` with `io.ReadAll(resp.Body)` without any maximum size limit, relying only on a 15-second client timeout. A malicious or MITM'd CA-issuer endpoint can stream data as fast as the network allows for the full timeout window, causing large memory allocation in the Runner process per triggered fetch.

### Finding Description
`Fetch` performs `f.client.Get(url)` and then `io.ReadAll(resp.Body)` with no `http.MaxBytesReader`/`io.LimitReader` cap, so response size is bounded only by wall-clock time (`defaultURLResolverFetchTimeout = 15s`) rather than bytes. [1](#0-0) 
The timeout constant and client construction show no other size control exists: [2](#0-1) 

The call is reached from `urlResolver.fetchIssuerCertificate`, which is invoked in a loop (bounded to `defaultURLResolverLoopLimit = 15` iterations) whenever `resolveFullChain` is enabled and a certificate has a non-nil `IssuingCertificateURL`: [3](#0-2) [4](#0-3) 

`BuildChainFromTLSConnectionState` calls this resolver against `tls.VerifiedChains`: [5](#0-4) 

However, the critical issue is **who controls the URL**: `IssuingCertificateURL` comes from the AIA extension of a certificate in `tls.VerifiedChains` — i.e., the leaf certificate presented by whatever TLS server the Runner is connecting to (per the question's framing, "this job's TLS setup"). This means the certificate — and therefore the AIA URL it embeds — originates from the server the job's HTTP client connects to, not from arbitrary job-controlled input like CI variables or YAML config. The attacker must control or MITM that TLS endpoint's certificate/server to point `IssuingCertificateURL` at a malicious streaming server, matching the stated precondition ("attacker controls or MITMs the issuer-cert HTTP endpoint").

Given that precondition is granted by the question, the missing size cap on `io.ReadAll` is real: nothing stops a malicious AIA server from streaming bytes continuously for the full 15-second `client.Timeout` window, and Go's `http.Client.Timeout` only bounds elapsed wall-clock time for the whole request/response cycle, not the number of bytes transferred. On a high-bandwidth link, this can allow tens/hundreds of MB to low-GB range of allocation per single fetch, and this can be repeated within the loop limit (up to 15 chained URL hops per chain-resolution call), and repeated across concurrent jobs/pipelines all connecting to the same malicious/MITM'd endpoint.

### Impact Explanation
This causes increased heap allocation in the Runner process handling TLS connection state processing for the affected job. Because `resolveFullChain` processing happens synchronously as part of building the CA chain for a job's HTTP client, and because Runner processes are typically shared across concurrently executing jobs (in many executors, e.g. shell/docker executors sharing one `gitlab-runner` process), sustained or repeated allocation from this path could contribute to memory pressure affecting job stability beyond a single job, satisfying "persistent multi-tenant disruption via memory exhaustion" if it can measurably impact the shared process. The impact is bounded by: (a) the 15s client timeout limiting each single fetch's duration, (b) the 15-iteration loop limit per chain resolution, and (c) Go's garbage collector reclaiming memory once each `Fetch` call returns and its buffer becomes unreachable — so this is a resource-consumption/DoS-class issue rather than a persistent leak, and its severity depends heavily on available bandwidth and concurrency of triggering connections.

### Likelihood Explanation
Feasible only if attacker satisfies the given precondition — control or MITM of the TLS endpoint whose certificate is presented in `tls.VerifiedChains`, with `resolveFullChain` enabled, and that certificate's AIA `IssuingCertificateURL` pointed at attacker infrastructure. This is a real, reachable code path with no test coverage for size-limiting `Fetch`; the fix is small and directly addresses the missing invariant ("no job-triggerable network response should cause unbounded resource consumption").

### Recommendation
Wrap `resp.Body` in `io.LimitReader(resp.Body, maxCertResponseSize)` (e.g., a few hundred KB — certificates are small) before calling `io.ReadAll`, and treat truncation/oversized reads as a fetch error. This bounds memory by size rather than only by time.

### Proof of Concept
```go
func TestHTTPFetcher_Fetch_UnboundedBody(t *testing.T) {
    srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        w.WriteHeader(http.StatusOK)
        buf := make([]byte, 1<<20) // 1MB chunks
        for {
            if _, err := w.Write(buf); err != nil {
                return
            }
            if f, ok := w.(http.Flusher); ok {
                f.Flush()
            }
        }
    }))
    defer srv.Close()

    f := newHTTPFetcher(2 * time.Second)
    data, err := f.Fetch(srv.URL)
    // Expect Fetch to either error out due to a size cap being exceeded,
    // or return data bounded by a defined max size constant.
    if err == nil {
        assert.LessOrEqual(t, len(data), maxAllowedCertBytes,
            "Fetch must not read unbounded response bodies")
    }
}
```
Expected (fixed) behavior: `Fetch` returns an error like "response body too large" once the limit is exceeded, instead of allocating unbounded memory for the duration of the client timeout.

### Citations

**File:** helpers/tls/ca_chain/resolver_url.go (L19-36)
```go
const defaultURLResolverLoopLimit = 15
const defaultURLResolverFetchTimeout = 15 * time.Second

type fetcher interface {
	Fetch(url string) ([]byte, error)
}

type httpFetcher struct {
	client *http.Client
}

func newHTTPFetcher(timeout time.Duration) *httpFetcher {
	return &httpFetcher{
		client: &http.Client{
			Timeout: timeout,
		},
	}
}
```

**File:** helpers/tls/ca_chain/resolver_url.go (L38-56)
```go
func (f *httpFetcher) Fetch(url string) ([]byte, error) {
	resp, err := f.client.Get(url)
	if resp != nil {
		defer func() { _ = resp.Body.Close() }()
	}
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("HTTP request failed with status code: %d", resp.StatusCode)
	}

	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	return data, nil
}
```

**File:** helpers/tls/ca_chain/resolver_url.go (L82-117)
```go
	loop := 0
	for {
		loop++
		if loop >= r.loopLimit {
			r.
				logger.
				Warning("urlResolver loop limit exceeded; exiting the loop")

			break
		}

		certificate := certs[len(certs)-1]
		log := prepareCertificateLogger(r.logger, certificate)

		if certificate.IssuingCertificateURL == nil {
			log.Debug("Certificate doesn't provide parent URL: exiting the loop")
			break
		}

		newCert, err := r.fetchIssuerCertificate(certificate)
		if err != nil {
			return nil, fmt.Errorf("error while fetching issuer certificate: %w", err)
		}

		if newCert == nil {
			log.Debug("Fetched issuer certificate file does not contain any certificates: exiting the loop")
			break
		}

		certs = append(certs, newCert)

		if isSelfSigned(newCert) {
			log.Debug("Fetched issuer certificate is a ROOT certificate so exiting the loop")
			break
		}
	}
```

**File:** helpers/tls/ca_chain/resolver_url.go (L122-137)
```go
func (r *urlResolver) fetchIssuerCertificate(cert *x509.Certificate) (*x509.Certificate, error) {
	log := prepareCertificateLogger(r.logger, cert).
		WithField("method", "fetchIssuerCertificate")

	issuerURL := cert.IssuingCertificateURL[0]

	log.WithField("issuerURL", issuerURL).Debug("Fetching issuer certificate")
	data, err := r.fetcher.Fetch(issuerURL)
	if err != nil {
		log.
			WithError(err).
			WithField("issuerURL", issuerURL).
			Warning("Remote certificate fetching error")

		return nil, fmt.Errorf("remote fetch failure: %w", err)
	}
```

**File:** helpers/tls/ca_chain/builder.go (L56-91)
```go
func (b *defaultBuilder) BuildChainFromTLSConnectionState(tls *tls.ConnectionState) error {
	for _, verifiedChain := range tls.VerifiedChains {
		b.logger.
			WithFields(logrus.Fields{
				"chain-leaf":         fmt.Sprintf("%v", verifiedChain),
				"resolve-full-chain": b.resolveFullChain,
			}).Debug("Processing chain")
		err := b.fetchCertificatesFromVerifiedChain(verifiedChain)
		if err != nil {
			return fmt.Errorf("error while fetching certificates into the CA Chain: %w", err)
		}
	}

	return nil
}

func (b *defaultBuilder) fetchCertificatesFromVerifiedChain(verifiedChain []*x509.Certificate) error {
	var err error

	if len(verifiedChain) < 1 {
		return nil
	}

	if b.resolveFullChain {
		verifiedChain, err = b.resolver.Resolve(verifiedChain)
		if err != nil {
			return fmt.Errorf("couldn't resolve certificates chain from the leaf certificate: %w", err)
		}
	}

	for _, certificate := range verifiedChain {
		b.addCertificate(certificate)
	}

	return nil
}
```
