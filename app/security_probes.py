"""Known automated vulnerability-probe requests.

These paths are not Pipka endpoints. Internet scanners send unsafe requests
to them looking for unrelated frameworks and developer tooling. Security
middleware must continue to reject and access-log them, while the operational
dashboard should not present the rejections as application failures.
"""

KNOWN_SECURITY_PROBE_PATHS = frozenset(
    {
        "/api/designer/v1/file-content",
        "/api/fs/exec",
        "/api/graphql",
        "/api/inngest",
        "/api/templates/preview",
        "/api/v1/validate/code",
    }
)


def is_known_security_probe(path: str, method: str, status_code: int) -> bool:
    """Return whether a rejected request is a known unrelated probe."""

    return (
        path in KNOWN_SECURITY_PROBE_PATHS
        and method.upper() in {"POST", "PUT", "PATCH", "DELETE"}
        and status_code in {403, 404}
    )
