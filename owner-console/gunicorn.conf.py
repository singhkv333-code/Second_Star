# One worker on purpose: sessions and sign-in throttles live in its memory.
# Threads serve the concurrency an owner console needs.
import gunicorn

gunicorn.SERVER = "console"          # no version in the Server header
bind = "0.0.0.0:8000"
workers = 1
threads = 8
timeout = 60
keepalive = 5
limit_request_line = 4096
limit_request_fields = 60
limit_request_field_size = 8190
accesslog = "-"
# The access log carries method, path, status and size; never query strings
# with codes or cookies.
access_log_format = '%(t)s "%(m)s %(U)s" %(s)s %(B)s %(L)ss'
forwarded_allow_ips = "*"
