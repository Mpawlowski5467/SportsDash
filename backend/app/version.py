"""The shipped application version, as a leaf module anything may import.

``app.routes.meta`` re-exports it under the name ``APP_VERSION`` that
``app.main`` and the tests already import, so the release checklist is
unchanged in spirit — one constant, one edit.  It lives here rather than in
the route module because ``app.useragent`` stamps the version into every
outbound request, and a provider importing ``app.routes.meta`` would drag
the whole route package (and with it the services and providers it wires)
into its own import graph.
"""

from __future__ import annotations

APP_VERSION = "1.4.0"
