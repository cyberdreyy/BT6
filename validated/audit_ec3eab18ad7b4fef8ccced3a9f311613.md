I was unable to complete verification of this finding due to running out of tool calls before retrieving the exact route-registration lines in `core/web/router.go` where the `/v2/vault/dkg_results/export` route is mounted.

What I confirmed:
- `VaultController.ExportDKGResult` in `core/web/vault_controller.go` (lines 89-119) has no internal role check in the handler itself — it directly reads the instance ID from the request body, looks up the DKG result package via `vault.NewVaultORM(vc.App.GetDB()).ReadResultPackage`, and returns the hex-encoded key-share package via `presenters.NewExportDKGResultResource`. [1](#0-0) 
- `core/web/router.go` does define role-wrapper helpers (`RequiresEditRole`, `RequiresAdminRole`, etc.) and does register vault routes somewhere in the file, but I was not able to retrieve and confirm the specific group (`authv1`/`authv2`) and role wrapper that the `/vault/dkg_results/export` route is nested under before running out of iterations. [2](#0-1) 

Because I cannot confirm from the code whether the export route is mounted inside a role-restricted sub-group (e.g., an `authv2` group already wrapped with `RequiresEditRole()` at a higher level) or is truly reachable by a bare view-role token, I cannot assert with the required certainty (exact file/function support) that this is an exploitable role-bypass. Making a determination here would require actually reading the specific `router.go` lines that register the `vault` routes and the group chain they are attached to.

I don't have enough verified evidence to output a definitive finding here — this would need a follow-up read of the exact `router.go` route-group registration for `/vault/dkg_results/export` to confirm or refute the claim. Given the ask-only/index constraints hit in this session, I'd recommend checking `core/web/router.go` directly (or starting a full Devin session with complete file access) to inspect the precise `Group(...)` nesting and role wrapper applied to the vault routes before treating this as confirmed.

### Citations

**File:** core/web/vault_controller.go (L91-119)
```go
func (vc *VaultController) ExportDKGResult(c *gin.Context) {
	var req ExportDKGResultRequest
	err := json.NewDecoder(c.Request.Body).Decode(&req)
	if err != nil {
		jsonAPIError(c, http.StatusBadRequest, errors.New("could not parse request body"))
		return
	}

	if req.InstanceID == "" {
		jsonAPIError(c, http.StatusBadRequest, errors.New("instanceId is required"))
		return
	}

	orm := vault.NewVaultORM(vc.App.GetDB())
	v, err := orm.ReadResultPackage(c.Request.Context(), dkgocrtypes.InstanceID(req.InstanceID))
	if err != nil {
		jsonAPIError(c, http.StatusNotFound, err)
		return
	}

	if v == nil {
		jsonAPIError(c, http.StatusNotFound, errors.New("DKG result not found"))
		return
	}

	hexPackage := hex.EncodeToString(v.ReportWithResultPackage)
	sha := sha256.Sum256(v.ReportWithResultPackage)
	shaStr := hex.EncodeToString(sha[:])
	jsonAPIResponse(c, presenters.NewExportDKGResultResource(hexPackage, shaStr), "exportDKGResult")
```

**File:** core/web/router.go (L1-45)
```go
package web

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"io/fs"
	"math"
	"net/http"
	"net/http/pprof"
	"net/url"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/Depado/ginprom"
	helmet "github.com/danielkov/gin-helmet"
	"github.com/gin-contrib/cors"
	"github.com/gin-contrib/expvar"
	"github.com/gin-contrib/sessions"
	"github.com/gin-contrib/sessions/cookie"
	limits "github.com/gin-contrib/size"
	"github.com/gin-gonic/gin"
	"github.com/graph-gophers/graphql-go"
	"github.com/graph-gophers/graphql-go/relay"
	"github.com/pkg/errors"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"github.com/ulule/limiter/v3"
	mgin "github.com/ulule/limiter/v3/drivers/middleware/gin"
	"github.com/ulule/limiter/v3/drivers/store/memory"
	"github.com/unrolled/secure"
	"go.opentelemetry.io/contrib/instrumentation/github.com/gin-gonic/gin/otelgin"
	"go.opentelemetry.io/otel"

	"github.com/smartcontractkit/chainlink/v2/core/build"
	"github.com/smartcontractkit/chainlink/v2/core/logger"
	"github.com/smartcontractkit/chainlink/v2/core/services/chainlink"
	"github.com/smartcontractkit/chainlink/v2/core/web/auth"
	"github.com/smartcontractkit/chainlink/v2/core/web/loader"
	"github.com/smartcontractkit/chainlink/v2/core/web/resolver"
	"github.com/smartcontractkit/chainlink/v2/core/web/schema"
)
```
