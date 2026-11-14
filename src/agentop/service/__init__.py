"""The agentop service: owns sessions and exposes them over HTTP and WebSocket."""

# The revision of /api/v2. Bump it when the API gains something a client may use;
# it only ever grows. A client built for a lower one tells the user to update.
# An incompatible change gets a new path, /api/v3, instead.
API_VERSION = 1
