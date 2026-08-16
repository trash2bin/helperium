package server

import "fmt"

// ValidateAuthTokens validates dashboard tokens before startup.
// Equal non-empty values erase the viewer/admin privilege boundary.
func ValidateAuthTokens(adminToken, viewerToken string) error {
	if adminToken != "" && viewerToken != "" && adminToken == viewerToken {
		return fmt.Errorf("ADMIN_TOKEN and VIEWER_TOKEN must differ")
	}
	return nil
}
