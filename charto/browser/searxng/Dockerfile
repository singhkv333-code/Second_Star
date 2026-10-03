# Our own SearXNG: the Browser widget's search engine when no Brave key is set.
# It answers only inside the private network (no public address). The secret
# is filled in at build time (provision_browser.sh) in a throwaway stage, so
# the final image keeps the image's own user and entrypoint.
FROM alpine:3.20 AS cfg
ARG SEARXNG_SECRET
COPY settings.yml /settings.yml
RUN test -n "$SEARXNG_SECRET" && sed -i "s/__SEARXNG_SECRET__/$SEARXNG_SECRET/" /settings.yml

FROM searxng/searxng:2026.10.2-19ffbcd30
COPY --from=cfg /settings.yml /etc/searxng/settings.yml
