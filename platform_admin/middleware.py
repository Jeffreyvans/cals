"""Stop platform staff opening protected institution data without consent.

The check runs before the view so a request with no grant never reaches the
data. The same call returns the grant used, which lets this middleware mark an
export with the grant id after the view has built its response.
"""

from .access import check_platform_institution_access


class PlatformDataAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        access = check_platform_institution_access(request)
        response = self.get_response(request)
        if access and access.get("export"):
            grant = access["grant"]
            response["X-EduSphere-Grant-Id"] = str(grant.pk)
            content_type = response.get("Content-Type", "")
            if "text/csv" in content_type and not getattr(response, "streaming", False):
                stamp = f"# Platform grant {grant.pk}: {grant.banner}\n".encode()
                response.content = stamp + response.content
                response["Content-Length"] = str(len(response.content))
        return response
