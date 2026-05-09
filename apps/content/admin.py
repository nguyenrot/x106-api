# SiteContent uses a composite primary key (app, section), which Django admin
# does not support. Manage via the API endpoints:
#   GET  /api/v1/admin/content/{app}
#   PUT  /api/v1/admin/content/{app}/{section}
